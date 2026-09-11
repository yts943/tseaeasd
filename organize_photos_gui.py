"""
사진 자동 분류 프로그램 - GUI 버전 (ttkbootstrap 디자인)
동일한 사진끼리는 서로 다른 폴더에 들어가도록 자동 배정.
백그라운드 스레드로 처리하여 스캔 중에도 화면이 멈추지 않음.
"""

import hashlib
import os
import queue
import shutil
import threading
from collections import defaultdict
from pathlib import Path

import ttkbootstrap as tb
from ttkbootstrap.constants import *
from tkinter import filedialog, messagebox, scrolledtext

IMAGE_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp",
    ".heic", ".heif", ".tif", ".tiff", ".jfif", ".avif",
}


# ---------- 핵심 로직 (GUI와 분리) ----------

def scan_images(source_dir: Path, log_fn):
    all_files = [p for p in source_dir.rglob("*") if p.is_file()]
    images = [p for p in all_files if p.suffix.lower() in IMAGE_EXTENSIONS]
    skipped = [p for p in all_files if p.suffix.lower() not in IMAGE_EXTENSIONS]

    log_fn(f"하위 폴더 포함 전체 파일 {len(all_files)}개 중 이미지 {len(images)}개 인식")
    if skipped:
        skip_exts = sorted(set(p.suffix.lower() or "(확장자 없음)" for p in skipped))
        log_fn(f"인식되지 않아 제외된 파일 {len(skipped)}개 (확장자: {', '.join(skip_exts)})")

    if not images:
        raise ValueError(f"'{source_dir}' 및 하위 폴더에서 이미지 파일을 찾지 못했습니다.")
    return images


def group_exact(files):
    buckets = defaultdict(list)
    for f in files:
        digest = hashlib.sha256(f.read_bytes()).hexdigest()
        buckets[digest].append(f)
    return list(buckets.values())


def group_perceptual(files, threshold, log_fn):
    from PIL import Image
    import imagehash

    hashes = {}
    for f in files:
        try:
            with Image.open(f) as img:
                hashes[f] = imagehash.phash(img, hash_size=16)
        except Exception as e:
            log_fn(f"'{f.name}' 처리 실패, 제외: {e}")

    items = list(hashes.items())
    visited = set()
    groups = []
    for i, (path_i, hash_i) in enumerate(items):
        if path_i in visited:
            continue
        group = [path_i]
        visited.add(path_i)
        for path_j, hash_j in items[i + 1:]:
            if path_j in visited:
                continue
            if hash_i - hash_j <= threshold:
                group.append(path_j)
                visited.add(path_j)
        groups.append(group)
    return groups


def assign_to_folders(groups, num_folders):
    loads = [0] * num_folders
    plan = defaultdict(list)
    warnings = []
    groups_sorted = sorted(groups, key=len, reverse=True)

    for group_id, group in enumerate(groups_sorted):
        k = len(group)
        if k > num_folders:
            warnings.append(
                f"그룹 {group_id} ({k}장, 대표파일: {group[0].name}): "
                f"폴더 수({num_folders})보다 많아 일부는 같은 폴더에 중복 배치됩니다."
            )
        folder_order = sorted(range(num_folders), key=lambda idx: loads[idx])
        for i, photo in enumerate(group):
            target = folder_order[i % num_folders]
            plan[target].append(photo)
            loads[target] += 1

    return plan, warnings, groups_sorted


def write_group_report(groups_sorted, report_path: Path):
    with open(report_path, "w", encoding="utf-8") as f:
        for group_id, group in enumerate(groups_sorted):
            f.write(f"[그룹 {group_id}] {len(group)}장\n")
            for p in group:
                f.write(f"  - {p.name}\n")
            f.write("\n")


def execute_plan(plan, dest_root: Path, num_folders: int, log_fn):
    for folder_idx in range(num_folders):
        target_dir = dest_root / str(folder_idx + 1)
        target_dir.mkdir(parents=True, exist_ok=True)
        for f in plan.get(folder_idx, []):
            shutil.copy2(str(f), str(target_dir / f.name))
        count = len(plan.get(folder_idx, []))
        log_fn(f"[{folder_idx + 1}] 폴더: {count}장 배치 완료")


# ---------- GUI ----------

class StatCard(tb.Frame):
    """숫자 + 캡션으로 구성된 작은 통계 카드"""

    def __init__(self, master, caption, bootstyle="secondary"):
        super().__init__(master, bootstyle=bootstyle, padding=14)
        self.value_var = tb.StringVar(value="-")
        tb.Label(
            self, textvariable=self.value_var, font=("Segoe UI", 22, "bold"),
            bootstyle=f"inverse-{bootstyle}",
        ).pack(anchor="w")
        tb.Label(
            self, text=caption, font=("Segoe UI", 10), bootstyle=f"inverse-{bootstyle}",
        ).pack(anchor="w")

    def set(self, value):
        self.value_var.set(str(value))


class App:
    def __init__(self, root: tb.Window):
        self.root = root
        root.title("사진 자동 분류 프로그램")
        root.geometry("920x720")
        root.minsize(820, 640)

        self.queue = queue.Queue()
        self._plan = None
        self._groups_sorted = None
        self._num_folders = None
        self._dest_root = None

        self._build_header()
        self._build_input_card()
        self._build_action_row()
        self._build_stats_row()
        self._build_log_area()

        self.root.after(80, self._poll_queue)

    # ---- 레이아웃 ----

    def _build_header(self):
        header = tb.Frame(self.root, padding=(24, 20, 24, 10))
        header.pack(fill="x")
        tb.Label(
            header, text="📸  사진 자동 분류 프로그램",
            font=("Segoe UI", 20, "bold"),
        ).pack(anchor="w")
        tb.Label(
            header,
            text="동일한 사진끼리는 서로 다른 폴더에, 원본은 손대지 않고 복사만 합니다.",
            font=("Segoe UI", 10), bootstyle="secondary",
        ).pack(anchor="w", pady=(2, 0))

    def _build_input_card(self):
        card = tb.Labelframe(self.root, text="설정", padding=18, bootstyle="secondary")
        card.pack(fill="x", padx=24, pady=(4, 12))
        card.columnconfigure(1, weight=1)

        tb.Label(card, text="원본 사진 폴더", font=("Segoe UI", 10, "bold")).grid(
            row=0, column=0, sticky="w", pady=6)
        self.source_var = tb.StringVar()
        tb.Entry(card, textvariable=self.source_var).grid(
            row=0, column=1, sticky="ew", padx=10)
        tb.Button(card, text="찾아보기", bootstyle="secondary-outline",
                   command=self.browse_source).grid(row=0, column=2)

        tb.Label(card, text="결과 저장 폴더", font=("Segoe UI", 10, "bold")).grid(
            row=1, column=0, sticky="w", pady=6)
        self.dest_var = tb.StringVar()
        tb.Entry(card, textvariable=self.dest_var).grid(
            row=1, column=1, sticky="ew", padx=10)
        tb.Button(card, text="찾아보기", bootstyle="secondary-outline",
                   command=self.browse_dest).grid(row=1, column=2)

        opt_row = tb.Frame(card)
        opt_row.grid(row=2, column=0, columnspan=3, sticky="w", pady=(12, 0))

        tb.Label(opt_row, text="폴더 개수", font=("Segoe UI", 10, "bold")).pack(
            side="left", padx=(0, 8))
        self.num_folders_var = tb.StringVar(value="100")
        tb.Entry(opt_row, textvariable=self.num_folders_var, width=8).pack(side="left")

        tb.Label(opt_row, text="   판별 방식", font=("Segoe UI", 10, "bold")).pack(
            side="left", padx=(24, 8))
        self.method_var = tb.StringVar(value="exact")
        tb.Radiobutton(opt_row, text="완전 동일 파일만 (안전)", variable=self.method_var,
                        value="exact", bootstyle="primary").pack(side="left", padx=4)
        tb.Radiobutton(opt_row, text="리사이즈·재압축 사진도 포함", variable=self.method_var,
                        value="perceptual", bootstyle="primary").pack(side="left", padx=4)

    def _build_action_row(self):
        row = tb.Frame(self.root, padding=(24, 0))
        row.pack(fill="x")
        tb.Button(row, text="🔍  미리보기", bootstyle="info-outline",
                   width=16, command=self.on_preview).pack(side="left")
        tb.Button(row, text="▶  실행 (실제 복사)", bootstyle="success",
                   width=20, command=self.on_run).pack(side="left", padx=10)
        self.open_folder_btn = tb.Button(
            row, text="📂  결과 폴더 열기", bootstyle="secondary-outline",
            width=16, command=self.open_result_folder, state="disabled")
        self.open_folder_btn.pack(side="left")

        self.progress = tb.Progressbar(row, mode="indeterminate", bootstyle="success-striped")
        self.progress.pack(side="left", fill="x", expand=True, padx=16)

    def _build_stats_row(self):
        row = tb.Frame(self.root, padding=(24, 12, 24, 4))
        row.pack(fill="x")
        row.columnconfigure((0, 1, 2), weight=1)

        self.stat_total = StatCard(row, "스캔된 사진", bootstyle="primary")
        self.stat_total.grid(row=0, column=0, sticky="ew", padx=(0, 6))

        self.stat_groups = StatCard(row, "고유 그룹 수", bootstyle="info")
        self.stat_groups.grid(row=0, column=1, sticky="ew", padx=6)

        self.stat_avg = StatCard(row, "폴더당 평균", bootstyle="success")
        self.stat_avg.grid(row=0, column=2, sticky="ew", padx=(6, 0))

    def _build_log_area(self):
        wrap = tb.Labelframe(self.root, text="진행 로그", padding=10, bootstyle="secondary")
        wrap.pack(fill="both", expand=True, padx=24, pady=(4, 20))
        self.log_widget = scrolledtext.ScrolledText(
            wrap, height=16, state="disabled", font=("Consolas", 10),
            bg="#1e1e1e", fg="#d4d4d4", insertbackground="#d4d4d4", borderwidth=0,
        )
        self.log_widget.pack(fill="both", expand=True)

    # ---- 로그/큐 ----

    def log(self, msg: str):
        self.queue.put(("log", msg))

    def set_stats(self, total, groups, avg):
        self.queue.put(("stats", (total, groups, avg)))

    def _poll_queue(self):
        try:
            while True:
                kind, payload = self.queue.get_nowait()
                if kind == "log":
                    self.log_widget.configure(state="normal")
                    self.log_widget.insert("end", payload + "\n")
                    self.log_widget.see("end")
                    self.log_widget.configure(state="disabled")
                elif kind == "stats":
                    total, groups, avg = payload
                    self.stat_total.set(total)
                    self.stat_groups.set(groups)
                    self.stat_avg.set(avg)
                elif kind == "done":
                    ok, message = payload
                    self.progress.stop()
                    if ok:
                        self.open_folder_btn.configure(state="normal")
                        messagebox.showinfo("완료", message)
                    else:
                        messagebox.showerror("오류", message)
        except queue.Empty:
            pass
        self.root.after(80, self._poll_queue)

    # ---- 액션 ----

    def browse_source(self):
        path = filedialog.askdirectory(title="원본 사진 폴더 선택")
        if path:
            self.source_var.set(path)
            if not self.dest_var.get():
                self.dest_var.set(str(Path(path).parent / "분류결과"))

    def browse_dest(self):
        path = filedialog.askdirectory(title="결과 저장 폴더 선택")
        if path:
            self.dest_var.set(path)

    def open_result_folder(self):
        if self._dest_root and self._dest_root.exists():
            os.startfile(str(self._dest_root))

    def _validate_inputs(self):
        source = self.source_var.get().strip()
        if not source:
            messagebox.showerror("입력 오류", "원본 사진 폴더를 선택하세요.")
            return None
        source_dir = Path(source)
        if not source_dir.exists():
            messagebox.showerror("입력 오류", f"'{source}' 경로를 찾을 수 없습니다.")
            return None

        dest = self.dest_var.get().strip() or str(source_dir.parent / "분류결과")
        try:
            num_folders = int(self.num_folders_var.get().strip())
        except ValueError:
            messagebox.showerror("입력 오류", "폴더 개수는 숫자로 입력하세요.")
            return None
        if num_folders <= 0:
            messagebox.showerror("입력 오류", "폴더 개수는 1 이상이어야 합니다.")
            return None

        return source_dir, Path(dest), num_folders

    def _run_pipeline(self, do_copy: bool):
        result = self._validate_inputs()
        if result is None:
            return
        source_dir, dest_root, num_folders = result
        method = self.method_var.get()

        self.progress.start(12)

        def worker():
            try:
                self.log("=" * 50)
                self.log("스캔을 시작합니다...")
                files = scan_images(source_dir, self.log)
                self.log(f"총 {len(files)}장의 이미지를 최종적으로 사용합니다.")

                groups = (group_perceptual(files, 4, self.log) if method == "perceptual"
                           else group_exact(files))
                self.log(f"{len(groups)}개의 서로 다른 사진 그룹으로 분류되었습니다.")

                plan, warnings, groups_sorted = assign_to_folders(groups, num_folders)
                for w in warnings:
                    self.log(f"[경고] {w}")

                counts = [len(plan.get(i, [])) for i in range(num_folders)]
                self.set_stats(len(files), len(groups), f"{sum(counts) / num_folders:.1f}")

                self._plan = plan
                self._groups_sorted = groups_sorted
                self._num_folders = num_folders
                self._dest_root = dest_root

                if not do_copy:
                    self.log("미리보기 완료. 결과가 이상 없으면 '실행'을 눌러 실제로 복사하세요.")
                    self.queue.put(("done", (True, "미리보기가 완료되었습니다. 로그를 확인하세요.")))
                    return

                dest_root.mkdir(parents=True, exist_ok=True)
                report_path = dest_root / "그룹_검수_리포트.txt"
                write_group_report(groups_sorted, report_path)
                self.log(f"그룹 검수 리포트 저장: {report_path}")

                self.log("실제 복사를 시작합니다...")
                execute_plan(plan, dest_root, num_folders, self.log)
                self.log("전체 작업 완료.")
                self.queue.put(("done", (True, f"작업이 완료되었습니다.\n결과 폴더: {dest_root}")))
            except Exception as e:
                self.log(f"[오류] {e}")
                self.queue.put(("done", (False, str(e))))

        threading.Thread(target=worker, daemon=True).start()

    def on_preview(self):
        self._run_pipeline(do_copy=False)

    def on_run(self):
        self._run_pipeline(do_copy=True)


def main():
    root = tb.Window(themename="flatly")
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
