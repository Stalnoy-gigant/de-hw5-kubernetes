"""Сверка полной таблицы и запись топ-10 в том же порядке, что в ДЗ 3."""
import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "results"


def read(name):
    with (ROOT / name).open(encoding="utf-8", newline="") as source:
        return [(row["skills"], int(row["count"])) for row in csv.DictReader(source)]


expected = read("hw3_mr_k2.csv")
actual = read("pairs.csv")
if actual[:10] != expected[:10]:
    raise SystemExit("Топ-10 отличается от ДЗ 3")
if actual != expected:
    raise SystemExit("Полные упорядоченные таблицы отличаются")
with (ROOT / "top10.csv").open("w", encoding="utf-8", newline="") as output:
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(("skills", "count"))
    writer.writerows(actual[:10])
print(f"top10=10/10 MATCH; all_pairs={len(actual)}/{len(expected)} MATCH")
for rank, (pair, count) in enumerate(actual[:10], 1):
    print(f"{rank:2}. {pair} — {count}")
