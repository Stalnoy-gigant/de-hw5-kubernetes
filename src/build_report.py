"""Сборка PDF из результатов и журналов кластера."""
import csv
from html import escape
from pathlib import Path
import re

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

ROOT = Path(__file__).resolve().parents[1]
MIB = 1 << 20
FONT_DIR = Path("/System/Library/Fonts/Supplemental")
if FONT_DIR.is_dir():
    fonts = [FONT_DIR / "Arial.ttf", FONT_DIR / "Arial Bold.ttf"]
else:
    fonts = [Path("/usr/share/fonts/truetype/dejavu") / font for font in ("DejaVuSans.ttf", "DejaVuSans-Bold.ttf")]
for name, file in zip(("Body", "Bold"), fonts):
    pdfmetrics.registerFont(TTFont(name, str(file)))
pdfmetrics.registerFontFamily("Body", normal="Body", bold="Bold")

DARK = "#15253B"
BLUE = "#174E78"
MUTED = "#44566A"
PALE = "#EDF4F9"
WHITE = "#FFFFFF"
W = A4[0] - 92
styles = {}


def style(name, font, size, leading, color, after=0, align=TA_LEFT):
    styles[name] = ParagraphStyle(name, fontName=font, fontSize=size, leading=leading,
                                  textColor=colors.HexColor(color), spaceAfter=after, alignment=align)


style("label", "Bold", 9, 12, BLUE, 7)
style("title", "Bold", 25, 29, DARK, 12)
style("heading", "Bold", 14, 18, DARK, 6)
style("body", "Body", 10.5, 15.5, DARK, 8)
style("note", "Body", 9, 13, MUTED, 5)
style("cell", "Body", 9, 13, DARK)
style("num", "Body", 9, 13, DARK, align=TA_RIGHT)
style("thead", "Bold", 9, 13, WHITE)
story = []


def p(value, key="body"):
    story.append(Paragraph(value, styles[key]))


def page(section, title):
    if story:
        story.append(PageBreak())
    p(section, "label")
    p(title, "title")


def table(headers, rows, widths, right=()):
    cells = [[Paragraph(escape(header), styles["thead"]) for header in headers]]
    for row in rows:
        cells.append([Paragraph(escape(str(value)), styles["num" if i in right else "cell"])
                      for i, value in enumerate(row)])
    t = Table(cells, colWidths=widths, repeatRows=1, hAlign="LEFT")
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(BLUE)),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.HexColor(PALE), colors.white]),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 9),
        ("RIGHTPADDING", (0, 0), (-1, -1), 9),
    ]))
    story.extend((t, Spacer(1, 11)))


def log(name):
    return (ROOT / "logs" / name).read_text()


def extract(pattern, document):
    return re.search(pattern, document, flags=re.MULTILINE)[1]


def lines(document, *parts):
    return [line for line in document.splitlines() if all(part in line for part in parts)]


def quote(label, text):
    p(f"<b>{label}</b>  {escape(text)}", "note")


def source(label, url):
    p(f'<link href="{url}" color="{BLUE}">{label}</link>', "note")


def number(value):
    return f"{int(value):,}".replace(",", " ")


def mib(value):
    return f"{int(value) / MIB:.1f}".replace(".", ",")


setup = log("01-setup.log")
memory = log("02-memory.log")
parallel = log("03-parallel.log")
scheduling = log("04-scheduling.log")
quota = log("05-quota.log")
peak = int(extract(r"measured_peak_bytes=(\d+)", memory))
tuned = int(extract(r"tuned_limit_mib=(\d+)", memory))
memory_peaks = re.findall(r"^memory_peak_bytes=(\d+)", memory, flags=re.MULTILINE)
reduce = extract(r"^(parts=8 rows=.*)$", parallel)
values = dict(re.findall(r"(\w+)=(\d+)", reduce))
size = extract(r"s3://hh-lake/clean/vacancies.jsonl (\d+) bytes", parallel)
capacity = extract(r" allocatable=(\S+)", scheduling)
allocated = extract(r" allocated_before=(\S+)", scheduling)
large = extract(r" low_and_high_request=(\S+)", scheduling)
version = extract(r"Ready\s+control-plane\s+\S+\s+(v\S+)", setup)
match = extract(r"^(top10=.*MATCH)$", parallel)
with (ROOT / "results/top10.csv").open(encoding="utf-8", newline="") as source_csv:
    pairs = list(csv.DictReader(source_csv))

page("ИНЖЕНЕРИЯ ДАННЫХ  /  ДЗ № 5", "Kubernetes. Пакетный расчёт и управление ресурсами")
p("Афанасьев Николай Игоревич · J3314 · ФТиИИ, Университет ИТМО", "label")
p("<b>Вариант 1.</b> Программист, разработчик на 1С · 18 ноября 2026 г.", "note")
p(f"На Mac с Apple Silicon поднят одноузловой кластер kind с Kubernetes {version} через Docker Desktop. "
  "Образ <b>de-hw5-pairs:1</b> собран для linux/arm64. В расчёте использована выборка IT-вакансий hh.ru из ДЗ 3.")
p("Заявки, лимиты и OOMKilled", "heading")
p("Во всех трёх Job использован один образ и один JSONL. Контейнер читает файл целиком и разбивает его на строки в памяти. "
  "CPU request — 250m, CPU limit — 1 ядро. Меняются только параметры памяти.")
table(("Job", "Memory request", "Memory limit", "Результат"), [
    ("pairs-oom", "48 MiB", "64 MiB", "OOMKilled, код 137"),
    ("pairs-profile", "256 MiB", "768 MiB", f"Completed, пик {mib(peak)} MiB"),
    ("pairs-tuned", "256 MiB", f"{tuned} MiB", f"Completed, пик {mib(memory_peaks[-1])} MiB"),
], [W * f for f in (.24, .21, .21, .34)])
p("В отчёте ДЗ 3 максимальный замер RSS для MapReduce с одним воркером составил <b>510,7 MiB</b>. "
  "Тот запуск также считал одиночные навыки и тройки, поэтому для профилирования здесь взят верхний предел 768 MiB. "
  f"Рабочий предел рассчитан по <b>memory.peak</b> cgroup контейнера. Измерено {number(peak)} байт "
  f"({mib(peak)} MiB). С запасом 25% и округлением вверх до 32 MiB получилось <b>{tuned} MiB</b>.")
p("При лимите 64 MiB Pod был назначен на узел и начал читать файл. Ядро остановило процесс после превышения "
  "лимита памяти контейнера. При новом лимите тот же расчёт завершился успешно.")
quote("Журнал logs/02-memory.log", extract(r"^(oom_reason=.*)$", memory))

page("02  /  ХРАНИЛИЩЕ И РАСЧЁТ", "Восемь частей из RustFS")
p(f"RustFS 1.0.1 перенесён из схемы Docker Compose в Kubernetes: Deployment, четыре PVC и внутренний Service "
  f"<b>rustfs:9000</b>. Все тома находятся на одном учебном узле. Компактный JSONL из ДЗ 3 загружен в "
  f"<b>hh-lake/clean/vacancies.jsonl</b> ({number(size)} байт).")
p("Indexed Job запускает 8 мапперов, до 4 одновременно. Индекс определяет диапазон байтов. "
  "Строка относится к части, в которой начинается: маппер пропускает обрезок первой строки и дочитывает последнюю. "
  "Каждый маппер сохраняет частоты в объект S3 по индексу. После завершения карты отдельный Job суммирует частоты "
  "и сортирует пары по убыванию частоты, при равенстве — по названию. В журнале видны две волны старта по четыре Pod.")
p(f"Обработано <b>{number(values['rows'])}</b> вакансий. Варианту соответствуют <b>{number(values['selected'])}</b>. "
  f"Получено <b>{number(values['distinct'])}</b> различных пар. Топ-10 и вся упорядоченная таблица "
  "совпали с сохранённым объектом <b>results/mr_k2.csv</b> из ДЗ 3.")
table(("Место", "Пара навыков", "Частота"), [
    (i, row["skills"], number(row["count"])) for i, row in enumerate(pairs, 1)
], [55, W - 123, 68], right=(2,))
quote("Журнал logs/03-parallel.log", match)

page("03  /  ПЛАНИРОВАНИЕ", "CPU, вытеснение и квота")
p("Почему задача ждала CPU?", "heading")
p(f"Узел объявил <b>{capacity}</b> allocatable CPU. До опыта Pod’ы уже запросили <b>{allocated}</b>. "
  f"Job <b>cpu-low</b> с приоритетом 100 запросил <b>{large}</b>, после чего для новых назначений осталось 100m. "
  "Job <b>cpu-wait</b> с заявкой 250m остался Pending. Планировщик сравнил заявки с allocatable, "
  "даже когда первый контейнер выполнял sleep.")
quote("Событие планировщика", lines(scheduling, "FailedScheduling", "Insufficient cpu")[0].split("default-scheduler", 1)[-1].strip())
p("Что изменил высокий приоритет?", "heading")
p(f"Job <b>cpu-high</b> запросил те же {large} с приоритетом 1000. Планировщик вытеснил Pod <b>cpu-low</b> "
  "и назначил Pod высокого приоритета. После фиксации события Job cpu-low удалён, чтобы его возможная "
  "замена не заняла CPU. После завершения cpu-high ожидавшая задача получила слот и завершилась.")
quote("Событие вытеснения", lines(scheduling, "Preempted")[0].split("Preempted", 1)[-1].strip())
quote("Источник", "logs/04-scheduling.log")
p("На каком этапе сработала квота?", "heading")
p("В пространстве <b>de-hw5-quota</b> задана <b>ResourceQuota requests.cpu = 500m</b>. "
  "Первый Pod занял 300m. Создание второго Pod с заявкой 300m превысило бы квоту на 100m. "
  "API отклонил его на этапе admission с HTTP 403. После удаления первого квота освободилась, "
  "и повторная подача того же манифеста успешно создала Pod.")
quote("Ответ API", lines(quota, "Forbidden", "exceeded quota")[0])
quote("Источник", "logs/05-quota.log")

page("04  /  СРАВНЕНИЕ", "Соответствия Kubernetes, YARN и Mesos")
table(("Механизм", "Kubernetes", "Apache Hadoop YARN", "Apache Mesos"), [
    ("Размещение", "kube-scheduler выбирает узел для Pod. kubelet запускает контейнеры.",
     "ResourceManager выделяет container по запросу ApplicationMaster. NodeManager запускает его.",
     "Master предлагает ресурсы. Scheduler фреймворка выбирает предложение, executor на agent запускает задачи."),
    ("Пакетный расчёт", "Job контролирует число успешных завершений. Indexed Job даёт индекс части.",
     "ApplicationMaster ведёт этапы, запрашивает контейнеры, обрабатывает сбои.",
     "Фреймворк ведёт этапы, сопоставляет задачи с offers и отслеживает их завершение."),
    ("Лимиты и доли", "Namespace и ResourceQuota ограничивают сумму requests при допуске Pod.",
     "Очередь CapacityScheduler задаёт гарантированную и максимальную ёмкость, делит ресурсы с другими очередями.",
     "Роли задают область квот. Распределение offers использует взвешенную справедливость по доминирующему ресурсу (wDRF)."),
], [88, 129, 137, W - 354])
p("<b>Как перенести этот расчёт?</b> В YARN ApplicationMaster запрашивает восемь контейнеров для диапазонов, "
  "затем запускает слияние. В Mesos scheduler фреймворка принимает offers и отправляет задачи executor’ам. "
  "В Kubernetes Indexed Job ведёт завершения мапперов, а запуск reduce после них задан в run.sh.")
p("<b>Как соотносятся приоритет и вытеснение?</b> В Kubernetes приоритет задан Pod через PriorityClass, "
  "а scheduler может вытеснить задачи на подходящем узле. В YARN CapacityScheduler политика вытеснения "
  "может вернуть ресурсы очереди, которой недостаёт положенной ёмкости. В Mesos порядок offers задаёт "
  "аллокатор, а политика запуска задач зависит от фреймворка.")
p("Документация", "heading")
source("Kubernetes · Job, включая Indexed Job", "https://kubernetes.io/docs/concepts/workloads/controllers/job/")
source("Kubernetes · Requests и limits", "https://kubernetes.io/docs/concepts/configuration/manage-resources-containers/")
source("Kubernetes · Priority и preemption", "https://kubernetes.io/docs/concepts/scheduling-eviction/pod-priority-preemption/")
source("Kubernetes · ResourceQuota", "https://kubernetes.io/docs/concepts/policy/resource-quotas/")
source("Apache Hadoop · YARN Architecture", "https://hadoop.apache.org/docs/current/hadoop-yarn/hadoop-yarn-site/YARN.html")
source("Apache Hadoop · CapacityScheduler", "https://hadoop.apache.org/docs/current/hadoop-yarn/hadoop-yarn-site/CapacityScheduler.html")
source("Apache Mesos · Architecture", "https://mesos.apache.org/documentation/latest/architecture/")
source("Apache Mesos · Roles", "https://mesos.apache.org/documentation/latest/roles/")
source("Apache Mesos · Quota", "https://mesos.apache.org/documentation/latest/quota/")


def footer(canvas, doc):
    canvas.setStrokeColor(colors.HexColor("#CCD8E2"))
    canvas.line(46, 39, A4[0] - 46, 39)
    canvas.setFillColor(colors.HexColor(MUTED))
    canvas.setFont("Body", 8)
    canvas.drawString(46, 25, "Инженерия данных  /  ДЗ № 5")
    canvas.drawRightString(A4[0] - 46, 25, f"{doc.page:02d}")


doc = SimpleDocTemplate(str(ROOT / "report/report.pdf"), pagesize=A4, leftMargin=46,
                        rightMargin=46, topMargin=43, bottomMargin=53,
                        title="ДЗ 5. Kubernetes и управление ресурсами",
                        author="Афанасьев Николай Игоревич")
doc.build(story, onFirstPage=footer, onLaterPages=footer)
