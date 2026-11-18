#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

context=kind-de-hw5
namespace=de-hw5
k() { kubectl --context "$context" "$@"; }
kn() { k -n "$namespace" "$@"; }
mark() { date '+%Y-%m-%d %H:%M:%S %z'; }
pod_for() {
  local pod
  for _ in {1..30}; do
    pod=$(kn get pods -l "job-name=$1" -o jsonpath='{.items[0].metadata.name}')
    if [[ -n "$pod" ]]; then
      echo "$pod"
      return
    fi
    sleep 1
  done
  return 1
}

setup() {
  local data=${1:-../hw3/data/vacancies.jsonl}
  test -s "$data"
  k get nodes -o wide
  docker image inspect de-hw5-pairs:1 --format 'image={{.Id}} os={{.Os}} arch={{.Architecture}}'
  local images platform expected actual
  platform=$(k get node -o jsonpath='{.items[0].status.nodeInfo.operatingSystem}/{.items[0].status.nodeInfo.architecture}')
  images=$(mktemp -d)
  (
    trap 'rm -rf "$images"' EXIT
    docker save --platform "$platform" de-hw5-pairs:1 -o "$images/pairs.tar"
    docker save --platform "$platform" rustfs/rustfs:1.0.1 -o "$images/rustfs.tar"
    kind load image-archive "$images/pairs.tar" --name de-hw5
    kind load image-archive "$images/rustfs.tar" --name de-hw5
  )
  k apply -f k8s/input.yaml
  kn wait --for=condition=ready pod/input-loader --timeout=120s
  expected=$(shasum -a 256 "$data")
  echo "$expected"
  kn cp "$data" input-loader:/input/vacancies.jsonl
  actual=$(kn exec input-loader -- sha256sum /input/vacancies.jsonl)
  echo "$actual"
  [[ "${expected%% *}" == "${actual%% *}" ]]
}

single_manifest() {
  sed -e "s/JOB_STAGE/$1/g" -e "s/JOB_REQUEST/$2/g" -e "s/JOB_LIMIT/$3/g" k8s/single.yaml | k create -f -
}

memory() {
  mark
  single_manifest oom 48Mi 64Mi
  kn wait --for=condition=failed job/pairs-oom --timeout=90s
  local pod reason code stats prior peak tuned
  pod=$(pod_for pairs-oom)
  kn get pod "$pod" -o wide
  reason=$(kn get pod "$pod" -o jsonpath='{.status.containerStatuses[0].state.terminated.reason}')
  code=$(kn get pod "$pod" -o jsonpath='{.status.containerStatuses[0].state.terminated.exitCode}')
  echo "oom_reason=$reason exit_code=$code request=48Mi limit=64Mi"
  kn logs --timestamps "$pod"
  [[ "$reason" == OOMKilled && "$code" == 137 ]]
  prior=${HW3_PEAK_MIB:-510.7}
  echo "hw3_rss_peak_mib=$prior source=hw3/results/profile_mr_w1.json"
  single_manifest profile 256Mi 768Mi
  kn wait --for=condition=complete job/pairs-profile --timeout=120s
  stats=$(kn logs job/pairs-profile)
  echo "$stats"
  peak=$(awk -F= '/^memory_peak_bytes=/{print $2}' <<< "$stats")
  [[ "$peak" =~ ^[0-9]+$ && "$peak" -gt 0 ]]
  tuned=$(( (peak * 5 + 4 * 32 * 1048576 - 1) / (4 * 32 * 1048576) * 32 ))
  echo "measured_peak_bytes=$peak safety_factor=1.25 rounding_mib=32 tuned_limit_mib=$tuned"
  single_manifest tuned 256Mi "${tuned}Mi"
  kn wait --for=condition=complete job/pairs-tuned --timeout=120s
  kn logs job/pairs-tuned
  kn get jobs pairs-oom pairs-profile pairs-tuned
  kn get pods -o custom-columns='NAME:.metadata.name,CPU_REQ:.spec.containers[0].resources.requests.cpu,MEM_REQ:.spec.containers[0].resources.requests.memory,MEM_LIMIT:.spec.containers[0].resources.limits.memory,STATE:.status.containerStatuses[0].state.terminated.reason'
}

parallel() {
  local prefix
  prefix="hw5/$(date -u +%Y%m%dT%H%M%SZ)"
  mark
  kn create secret generic rustfs-auth --from-literal=S3_ACCESS_KEY=rustfsadmin \
      --from-literal=S3_SECRET_KEY=rustfsadmin --dry-run=client -o yaml | kn apply -f -
  # envFrom is evaluated when the loader container starts.
  kn delete pod input-loader --wait=true
  k apply -f k8s/input.yaml
  kn wait --for=condition=ready pod/input-loader --timeout=120s
  k apply -f k8s/rustfs.yaml
  kn rollout status deployment/rustfs --timeout=120s
  kn get pods,svc,pvc -o wide
  kn exec input-loader -- python /app/pairs.py upload
  sed "s|RUN_PREFIX|$prefix|g" k8s/parallel.yaml | k create -f -
  kn wait --for=condition=complete job/pairs-map --timeout=120s
  kn get job pairs-map -o jsonpath='parallelism={.spec.parallelism} completions={.spec.completions} completedIndexes={.status.completedIndexes}{"\n"}'
  kn get pods -l job-name=pairs-map -o wide
  kn get pods -l job-name=pairs-map -o custom-columns='NAME:.metadata.name,STARTED:.status.containerStatuses[0].state.terminated.startedAt,FINISHED:.status.containerStatuses[0].state.terminated.finishedAt'
  kn logs -l job-name=pairs-map --prefix=true --timestamps=true --max-log-requests=8
  sed "s|RUN_PREFIX|$prefix|g" k8s/reduce.yaml | k create -f -
  kn wait --for=condition=complete job/pairs-reduce --timeout=120s
  kn logs job/pairs-reduce
  kn exec input-loader -- python /app/pairs.py result --prefix "$prefix" > results/pairs.csv.tmp
  mv results/pairs.csv.tmp results/pairs.csv
  python3 src/verify.py
}

schedule_job() {
  sed -e "s/DEMO_ROLE/$1/g" -e "s/DEMO_CPU/$2/g" -e "s/DEMO_SECONDS/$3/g" k8s/schedule.yaml | k create -f -
}

scheduling() {
  k apply -f k8s/priorities.yaml
  local node capacity allocated large low wait high events
  node=$(k get node -o jsonpath='{.items[0].metadata.name}')
  capacity=$(k get node "$node" -o jsonpath='{.status.allocatable.cpu}')
  allocated=$(k describe node "$node" | awk '/^Allocated resources:/{table=1} table && $1=="cpu"{print $2; exit}')
  cpu_milli() { if [[ "$1" == *m ]]; then echo "${1%m}"; else echo "$(($1 * 1000))"; fi; }
  large=$(( $(cpu_milli "$capacity") - $(cpu_milli "$allocated") - 100 ))
  [[ "$large" -ge 500 ]]
  echo "node=$node allocatable=$capacity allocated_before=$allocated low_and_high_request=${large}m wait_request=250m expected_remaining=100m"
  mark
  schedule_job low "${large}m" 180
  low=$(pod_for cpu-low)
  kn wait --for=condition=ready "pod/$low" --timeout=75s
  schedule_job wait 250m 2
  wait=$(pod_for cpu-wait)
  kn wait --for=condition=PodScheduled=False "pod/$wait" --timeout=45s
  mark
  kn get pods "$low" "$wait" -o wide
  events=$(kn describe pod "$wait")
  printf '%s\n' "$events" | sed -n '/^Events:/,$p'
  [[ "$events" == *"Insufficient cpu"* ]]
  schedule_job high "${large}m" 18
  high=$(pod_for cpu-high)
  kn wait --for=condition=ready "pod/$high" --timeout=75s
  mark
  kn get pods "$high" "$wait" -o wide
  events=$(kn get events --field-selector "involvedObject.name=$low")
  echo "$events"
  [[ "$events" == *Preempted* ]]
  # При вытеснении контроллер может создать замену. Удаляем только свой Job,
  # чтобы после завершения высокого приоритета очередь досталась cpu-wait.
  kn delete job cpu-low
  kn wait --for=condition=complete job/cpu-high job/cpu-wait --timeout=90s
  mark
  kn logs --timestamps job/cpu-high
  kn logs --timestamps job/cpu-wait
  kn get events --field-selector "involvedObject.name=$high"
  kn get events --field-selector "involvedObject.name=$wait"
}

quota() {
  k apply -f k8s/quota.yaml
  local output
  k -n de-hw5-quota wait --for=jsonpath='{.status.hard.requests\.cpu}'=500m resourcequota/cpu-budget --timeout=60s
  sed 's/DEMO_ID/a/g' k8s/quota-pod.yaml | k create -f -
  k -n de-hw5-quota describe resourcequota cpu-budget
  if output=$(sed 's/DEMO_ID/b/g' k8s/quota-pod.yaml | k create -f - 2>&1); then
    echo "Второй Pod неожиданно принят" >&2
    return 1
  fi
  echo "$output"
  [[ "$output" == *Forbidden* && "$output" == *"exceeded quota"* ]]
  k -n de-hw5-quota delete pod quota-a
  k -n de-hw5-quota wait --for=jsonpath='{.status.used.requests\.cpu}'=0 resourcequota/cpu-budget --timeout=60s
  sed 's/DEMO_ID/b/g' k8s/quota-pod.yaml | k create -f -
  k -n de-hw5-quota wait --for=condition=ready pod/quota-b --timeout=60s
  k -n de-hw5-quota get pods -o wide
  k -n de-hw5-quota describe resourcequota cpu-budget
  k -n de-hw5-quota delete pod quota-b
}

mkdir -p logs results
case "${1:-}" in
  setup) setup "${2:-}" 2>&1 | tee logs/01-setup.log ;;
  memory) memory 2>&1 | tee logs/02-memory.log ;;
  parallel) parallel 2>&1 | tee logs/03-parallel.log ;;
  scheduling) scheduling 2>&1 | tee logs/04-scheduling.log ;;
  quota) quota 2>&1 | tee logs/05-quota.log ;;
  *) echo "Usage: $0 {setup [jsonl]|memory|parallel|scheduling|quota}" >&2; exit 2 ;;
esac
