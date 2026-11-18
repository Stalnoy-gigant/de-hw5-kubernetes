"""Правила фильтрации и подсчёта пар из hw3/src/mr.py."""
import argparse
import csv
import io
import itertools
import json
import os
from pathlib import Path
import re
import time
from collections import Counter

from common import BUCKET, CLEAN_KEY, NAME_RE, ROLE, SKILL_RE, s3

NAME = re.compile(NAME_RE, re.I)
SKILL = re.compile(SKILL_RE, re.I)


def count_pairs(lines):
    counts = Counter()
    rows = selected = 0
    for line in lines:
        if not line.strip():
            continue
        record = json.loads(line)
        rows += 1
        skills = record["skills"]
        if record["role"] != ROLE:
            continue
        if not (NAME.search(record["name"]) or any(SKILL.match(skill) for skill in skills)):
            continue
        selected += 1
        unique = sorted({skill.lower() for skill in skills})
        counts.update(" | ".join(pair) for pair in itertools.combinations(unique, 2))
    return rows, selected, counts


def range_lines(client, start, end, size, etag):
    """Каждая строка принадлежит диапазону, в котором начинается её первый байт."""
    if start == end:
        return

    def read(lo, hi):
        response = client.get_object(Bucket=BUCKET, Key=CLEAN_KEY, IfMatch=etag,
                                     Range=f"bytes={lo}-{hi - 1}")
        with response["Body"] as body:
            return body.read()

    lo = max(0, start - 1)
    hi = min(size, end + (64 << 10))
    data = read(lo, hi)
    if start and data[0] != 10:
        first_end = data.find(b"\n")
        if first_end < 0:
            return
        position = first_end + 1
    else:
        position = start - lo
    while position < end - lo:
        line_end = data.find(b"\n", position)
        while line_end < 0 and hi < size:
            next_hi = min(size, hi + (64 << 10))
            data += read(hi, next_hi)
            line_end = data.find(b"\n", hi - lo)
            hi = next_hi
        if line_end < 0:
            yield data[position:]
            return
        yield data[position:line_end]
        position = line_end + 1


def csv_bytes(counts):
    csv_file = io.StringIO(newline="")
    writer = csv.writer(csv_file, lineterminator="\n")
    writer.writerow(("skills", "count"))
    writer.writerows(sorted(counts.items(), key=lambda item: (-item[1], item[0])))
    return csv_file.getvalue().encode()


def profile():
    path = Path("/sys/fs/cgroup/memory.peak")
    if not path.exists():
        path = Path("/sys/fs/cgroup/memory/memory.max_usage_in_bytes")
    if path.exists():
        print(f"memory_peak_bytes={path.read_text().strip()}")


def single(path):
    print(f"Читаю файл {path} ({path.stat().st_size} байт) целиком")
    start = time.monotonic()
    rows, selected, counts = count_pairs(path.read_bytes().splitlines())
    print(f"rows={rows} selected={selected} distinct={len(counts)} elapsed={time.monotonic()-start:.3f}s")
    Path("/tmp/pairs.csv").write_bytes(csv_bytes(counts))
    profile()


def upload(path):
    client = s3()
    if BUCKET not in {item["Name"] for item in client.list_buckets()["Buckets"]}:
        client.create_bucket(Bucket=BUCKET)
    client.upload_file(str(path), BUCKET, CLEAN_KEY)
    print(f"s3://{BUCKET}/{CLEAN_KEY} {client.head_object(Bucket=BUCKET, Key=CLEAN_KEY)['ContentLength']} bytes")


def map_part(prefix, parts, index):
    client = s3()
    obj = client.head_object(Bucket=BUCKET, Key=CLEAN_KEY)
    size, etag = obj["ContentLength"], obj["ETag"]
    start, end = size * index // parts, size * (index + 1) // parts
    rows, selected, counts = count_pairs(range_lines(client, start, end, size, etag))
    body = json.dumps({"etag": etag, "rows": rows, "selected": selected, "counts": counts}, ensure_ascii=False)
    client.put_object(Bucket=BUCKET, Key=f"{prefix}/parts/{index}.json", Body=body.encode())
    print(f"index={index} range=[{start},{end}) rows={rows} selected={selected} distinct={len(counts)}")
    profile()


def reduce_parts(prefix, parts):
    client = s3()
    etag = client.head_object(Bucket=BUCKET, Key=CLEAN_KEY)["ETag"]
    rows = selected = 0
    total = Counter()
    for index in range(parts):
        response = client.get_object(Bucket=BUCKET, Key=f"{prefix}/parts/{index}.json")
        with response["Body"] as body:
            part = json.load(body)
        if part["etag"] != etag:
            raise ValueError(f"Часть {index} рассчитана по другой версии файла")
        rows += part["rows"]
        selected += part["selected"]
        total.update(part["counts"])
    client.put_object(Bucket=BUCKET, Key=f"{prefix}/pairs.csv", Body=csv_bytes(total))
    print(f"parts={parts} rows={rows} selected={selected} distinct={len(total)} occurrences={sum(total.values())}")
    print(f"result=s3://{BUCKET}/{prefix}/pairs.csv")
    profile()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("single", "upload", "map", "reduce", "result"))
    parser.add_argument("--path", type=Path, default=Path("/input/vacancies.jsonl"))
    parser.add_argument("--prefix", default="hw5")
    parser.add_argument("--parts", type=int, default=8)
    args = parser.parse_args()
    if args.mode == "single":
        single(args.path)
    elif args.mode == "upload":
        upload(args.path)
    elif args.mode == "map":
        map_part(args.prefix, args.parts, int(os.environ["JOB_COMPLETION_INDEX"]))
    elif args.mode == "reduce":
        reduce_parts(args.prefix, args.parts)
    else:
        import sys
        response = s3().get_object(Bucket=BUCKET, Key=f"{args.prefix}/pairs.csv")
        with response["Body"] as body:
            sys.stdout.buffer.write(body.read())


if __name__ == "__main__":
    main()
