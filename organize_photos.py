"""
사진 자동 분류 프로그램 (대화형, exe 빌드 대상)

- 동일한 사진끼리는 서로 다른 폴더에 들어가도록 자동 배정
- 결과 폴더명은 자연수(1, 2, 3 ... N)로 생성되어 탐색기에서 확인이 쉬움
- 원본은 손대지 않고 항상 '복사'만 함 (안전 우선)
"""

import hashlib
import logging
import shutil
import sys
from collections import defaultdict
from pathlib import Path

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


def ask(prompt: str, default: str = "") -> str:
    suffix = f" (기본값: {default})" if default else ""
    value = input(f"{prompt}{suffix}: ").strip()
    return value if value else default


def scan_images(source_dir: Path) -> list[Path]:
    files = [p for p in source_dir.iterdir()
             if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS]
    if not files:
        raise ValueError(f"'{source_dir}' 에서 이미지 파일을 찾지 못했습니다.")
    return files


def group_exact(files: list[Path]) -> list[list[Path]]:
    """파일 바이트 단위 SHA-256 해시로 완전 동일 파일만 그룹핑. 오탐 불가능."""
    buckets: dict[str, list[Path]] = defaultdict(list)
    for f in files:
        digest = hashlib.sha256(f.read_bytes()).hexdigest()
        buckets[digest].append(f)
    return list(buckets.values())


def group_perceptual(files: list[Path], threshold: int = 4) -> list[list[Path]]:
    """pHash 해밍 거리 기준 유사 이미지 그룹핑 (리사이즈/재압축된 사진 포함)"""
    from PIL import Image
    import imagehash

    hashes = {}
    for f in files:
        try:
            with Image.open(f) as img:
                hashes[f] = imagehash.phash(img, hash_size=16)
        except Exception as e:
            logger.warning(f"'{f.name}' 처리 실패, 제외: {e}")

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


def assign_to_folders(groups: list[list[Path]], num_folders: int):
    """같은 그룹(=동일 사진)의 구성원은 반드시 서로 다른 폴더에 배정, 총량은 균등하게"""
    loads = [0] * num_folders
    plan: dict[int, list[Path]] = defaultdict(list)
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


def execute_plan(plan, dest_root: Path, num_folders: int):
    for folder_idx in range(num_folders):
        target_dir = dest_root / str(folder_idx + 1)  # 폴더명: 1, 2, 3 ... 자연수
        target_dir.mkdir(parents=True, exist_ok=True)
        for f in plan.get(folder_idx, []):
            shutil.copy2(str(f), str(target_dir / f.name))
        count = len(plan.get(folder_idx, []))
        logger.info(f"[{folder_idx + 1}] 폴더: {count}장 배치 완료")


def main():
    print("=== 사진 자동 분류 프로그램 ===")
    print("동일한 사진끼리는 서로 다른 폴더에 들어가도록 자동으로 나눠줍니다.")
    print("(원본은 그대로 두고, 결과 폴더에 복사만 합니다)\n")

    source = ask("원본 사진이 있는 폴더 경로를 입력하세요 (예: C:\\Photos)")
    if not source:
        print("경로가 입력되지 않아 종료합니다.")
        input("아무 키나 눌러 종료...")
        sys.exit(1)
    source_dir = Path(source)
    if not source_dir.exists():
        print(f"'{source}' 경로를 찾을 수 없습니다.")
        input("아무 키나 눌러 종료...")
        sys.exit(1)

    dest = ask("결과를 저장할 폴더 경로", str(source_dir.parent / "분류결과"))
    dest_root = Path(dest)

    folders_str = ask("생성할 폴더 개수", "100")
    num_folders = int(folders_str)

    method_str = ask(
        "판별 방식 선택 -> 1: 완전히 동일한 파일만(안전, 기본값) / 2: 리사이즈·재압축된 사진도 포함",
        "1",
    )

    print("\n작업을 시작합니다...\n")

    files = scan_images(source_dir)
    logger.info(f"총 {len(files)}장의 이미지를 찾았습니다.")

    if method_str == "2":
        groups = group_perceptual(files)
    else:
        groups = group_exact(files)

    logger.info(f"{len(groups)}개의 서로 다른 사진 그룹으로 분류되었습니다.")

    plan, warnings, groups_sorted = assign_to_folders(groups, num_folders)

    dest_root.mkdir(parents=True, exist_ok=True)
    report_path = dest_root / "그룹_검수_리포트.txt"
    write_group_report(groups_sorted, report_path)

    for w in warnings:
        logger.warning(w)

    execute_plan(plan, dest_root, num_folders)

    print(f"\n완료되었습니다. 결과 폴더: {dest_root}")
    print(f"그룹핑 검수 리포트: {report_path}")
    input("\n아무 키나 눌러 종료...")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\n오류가 발생했습니다: {e}")
        input("아무 키나 눌러 종료...")
