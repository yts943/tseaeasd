"""
사진 자동 분류 프로그램 - GUI 버전
미리보기: 실제 복사 전에 그룹핑 결과와 폴더별 배치 예상치를 확인
실행: 미리보기와 동일한 로직으로 실제 복사 수행
"""

import hashlib
import shutil
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk
from collections import defaultdict
from pathlib import Path

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

class App:
    def __init__(self, root):
        self.root = root
        root.title("사진 자동 분류 프로그램")
        root.geometry("760x580")

        pad = {"padx": 8, "pady": 6}

        frm = ttk.Frame(root)
        frm.pack(fill="x", **pad)

        ttk.Label(frm, text="원본 사진 폴더").grid(row=0, column=0, sticky="w")
        self.source_var = tk.StringVar()
        ttk.Entry(frm, textvariable=self.source_var, width=60).grid(row=0, column=1, padx=4)
        ttk.Button(frm, text="찾아보기", command=self.browse_source).grid(row=0, column=2)

        ttk.Label(frm, text="결과 저장 폴더").grid(row=1, column=0, sticky="w")
        self.dest_var = tk.StringVar()
        ttk.Entry(frm, textvariable=self.dest_var, width=60).grid(row=1, column=1, padx=4)
        ttk.Button(frm, text="찾아보기", command=self.browse_dest).grid(row=1, column=2)

        ttk.Label(frm, text="폴더 개수").grid(row=2, column=0, sticky="w")
        self.num_folders_var = tk.StringVar(value="100")
        ttk.Entry(frm, textvariable=self.num_folders_var, width=10).grid(row=2, column=1, sticky="w")

        ttk.Label(frm, text="판별 방식").grid(row=3, column=0, sticky="w")
        self.method_var = tk.StringVar(value="exact")
        ttk.Radiobutton(frm, text="완전히 동일한 파일만 (안전)", variable=self.method_var,
                         value="exact").grid(row=3, column=1, sticky="w")
        ttk.Radiobutton(frm, text="리사이즈/재압축된 사진도 포함", variable=self.method_var,
                         value="perceptual").grid(row=4, column=1, sticky="w")

        btn_frm = ttk.Frame(root)
        btn_frm.pack(fill="x", **pad)
        ttk.Button(btn_frm, text="미리보기", command=self.on_preview).pack(side="left", padx=4)
        ttk.Button(btn_frm, text="실행 (실제 복사)", command=self.on_run).pack(side="left", padx=4)

        self.log_widget = scrolledtext.ScrolledText(root, height=26, state="disabled")
        self.log_widget.pack(fill="both", expand=True, **pad)

        self._plan = None
        self._groups_sorted = None
        self._num_folders = None
        self._dest_root = None

    def log(self, msg: str):
        self.log_widget.configure(state="normal")
        self.log_widget.insert("end", msg + "\n")
        self.log_widget.see("end")
        self.log_widget.configure(state="disabled")
        self.root.update_idletasks()

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

    def _compute(self):
        result = self._validate_inputs()
        if result is None:
            return None
        source_dir, dest_root, num_folders = result

        self.log("=" * 50)
        self.log("스캔을 시작합니다...")
        files = scan_images(source_dir, self.log)
        self.log(f"총 {len(files)}장의 이미지를 최종적으로 사용합니다.")

        if self.method_var.get() == "perceptual":
            groups = group_perceptual(files, threshold=4, log_fn=self.log)
        else:
            groups = group_exact(files)

        self.log(f"{len(groups)}개의 서로 다른 사진 그룹으로 분류되었습니다.")

        plan, warnings, groups_sorted = assign_to_folders(groups, num_folders)
        for w in warnings:
            self.log(f"[경고] {w}")

        counts = [len(plan.get(i, [])) for i in range(num_folders)]
        self.log(
            f"폴더별 배치 예상: 최소 {min(counts)}장 / 최대 {max(counts)}장 / "
            f"평균 {sum(counts) / num_folders:.1f}장"
        )
        preview_line = ", ".join(f"{i + 1}번:{c}장" for i, c in enumerate(counts[:10]))
        self.log(f"앞 10개 폴더 예시 -> {preview_line} ...")

        self._plan = plan
        self._groups_sorted = groups_sorted
        self._num_folders = num_folders
        self._dest_root = dest_root
        return True

    def on_preview(self):
        try:
            self._compute()
            self.log("미리보기 완료. 결과가 이상 없으면 '실행'을 눌러 실제로 복사하세요.")
        except Exception as e:
            self.log(f"[오류] {e}")
            messagebox.showerror("오류", str(e))

    def on_run(self):
        try:
            if self._compute() is None:
                return
            self._dest_root.mkdir(parents=True, exist_ok=True)
            report_path = self._dest_root / "그룹_검수_리포트.txt"
            write_group_report(self._groups_sorted, report_path)
            self.log(f"그룹 검수 리포트 저장: {report_path}")

            self.log("실제 복사를 시작합니다...")
            execute_plan(self._plan, self._dest_root, self._num_folders, self.log)
            self.log("전체 작업 완료.")
            messagebox.showinfo("완료", f"작업이 완료되었습니다.\n결과 폴더: {self._dest_root}")
        except Exception as e:
            self.log(f"[오류] {e}")
            messagebox.showerror("오류", str(e))


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
