import os, glob

folder = "/source/data/gosapteka56/2026-10-27/rag-prepared"
files = sorted(os.listdir(folder))

for f in files:
    path = os.path.join(folder, f)
    if os.path.isfile(path):
        size = os.path.getsize(path)
        with open(path, "r", encoding="utf-8", errors="ignore") as fp:
            first_line = fp.readline().strip()
            # find heading 1
            h1 = first_line
            if not h1.startswith("#"):
                for line in fp:
                    if line.startswith("# "):
                        h1 = line.strip()
                        break
        print(f"{f:70s} | {size:6d} B | {h1[:60]}")
