try:  # mistralai v1.x
    from mistralai import Mistral, DocumentURLChunk
except ImportError:  # mistralai v2.x moved SDK under mistralai.client
    from mistralai.client.sdk import Mistral
    from mistralai.client.models import DocumentURLChunk
from pathlib import Path
import os
import base64
import sys
import urllib.parse
import argparse
from tqdm import tqdm
import time
from typing import Optional, Set, Callable

# Helpers

def parse_page_range(expr: Optional[str]) -> Optional[Set[int]]:
    if not expr:
        return None
    selected: Set[int] = set()
    for part in expr.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            try:
                start, end = int(a), int(b)
            except ValueError:
                raise ValueError(f"Invalid page range segment: '{part}'")
            if start <= 0 or end <= 0 or end < start:
                raise ValueError(f"Invalid page range segment: '{part}'")
            selected.update(range(start, end + 1))
        else:
            try:
                n = int(part)
            except ValueError:
                raise ValueError(f"Invalid page number: '{part}'")
            if n <= 0:
                raise ValueError(f"Invalid page number: '{part}'")
            selected.add(n)
    return selected or None

def retry(call: Callable[[], object], description: str, attempts: int = 3, delay: float = 1.0, backoff: float = 2.0, show_progress: bool = True):
    last_exc = None
    for i in range(attempts):
        try:
            return call()
        except Exception as e:
            last_exc = e
            if i == attempts - 1:
                break
            if show_progress:
                tqdm.write(f"Retrying {description} ({i + 1}/{attempts}) due to: {e}")
            time.sleep(delay)
            delay *= backoff
    raise last_exc  # type: ignore

def replace_images_in_markdown(markdown_str: str, images_dict: dict) -> str:
    for img_name, img_path in images_dict.items():
        markdown_str = markdown_str.replace(f"![{img_name}]({img_name})", f"![{img_name}]({img_path})")
    return markdown_str

def process_pdf_to_md(
    pdf_path: str,
    api_key: str,
    show_progress: bool = True,
    images_base_dir: Optional[Path] = None,
    images_subdir: Optional[str] = None,
    inline_images: bool = False,
    page_range: Optional[Set[int]] = None,
    separator: str = "\n\n",
    retries: int = 3,
    timeout: Optional[float] = None,
) -> str:
    # Initialize client
    client = Mistral(api_key=api_key)
    
    # Confirm PDF file exists
    pdf_file = Path(pdf_path)
    if not pdf_file.is_file():
        raise FileNotFoundError(f"PDF file does not exist: {pdf_path}")
    pdf_name = pdf_file.stem

    # Compute where images should be saved (relative to output .md location when provided)
    base_dir = images_base_dir or pdf_file.parent
    images_dir_name = images_subdir or f"{pdf_name}_images"
    images_dir = base_dir / images_dir_name
    images_dir_created = False  # lazy-create only when needed

    if show_progress:
        tqdm.write(f"Processing {pdf_path}...")

    # Upload with retries
    uploaded_file = retry(
        lambda: client.files.upload(
            file={"file_name": pdf_name, "content": pdf_file.read_bytes()},
            purpose="ocr",
        ),
        description="file upload",
        attempts=retries,
        show_progress=show_progress,
    )

    signed_url = retry(
        lambda: client.files.get_signed_url(file_id=uploaded_file.id, expiry=1),
        description="signed URL retrieval",
        attempts=retries,
        show_progress=show_progress,
    )

    pdf_response = retry(
        lambda: client.ocr.process(
            document=DocumentURLChunk(document_url=signed_url.url),
            model="mistral-ocr-latest",
            include_image_base64=True,
        ),
        description="OCR processing",
        attempts=retries,
        show_progress=show_progress,
    )

    # Process pages and images
    all_markdowns = []
    pages = pdf_response.pages
    selected_pages = (
        [p for i, p in enumerate(pages, start=1) if not page_range or i in page_range]
    )

    # Use position=1 for page progress to keep overall progress at bottom
    for page in tqdm(
        selected_pages,
        desc=f"Converting pages of {Path(pdf_path).name}",
        leave=False,
        disable=not show_progress,
        position=1 if show_progress else None,
    ):
        page_images = {}
        for img in page.images:
            # Parse data URI if present
            img_b64 = img.image_base64 or ""
            if "," in img_b64 and img_b64.startswith("data:"):
                header, b64_part = img_b64.split(",", 1)
                mime = header.split(";")[0].split(":", 1)[-1] or "image/png"
            else:
                b64_part = img_b64.split(",", 1)[-1] if "," in img_b64 else img_b64
                mime = "image/png"

            # Decide extension from mime
            ext = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}.get(mime, "png")

            if inline_images:
                # Use data URI directly
                page_images[img.id] = f"data:{mime};base64,{b64_part}"
            else:
                # Save to disk lazily
                if not images_dir_created:
                    os.makedirs(images_dir, exist_ok=True)
                    images_dir_created = True
                img_bytes = base64.b64decode(b64_part) if b64_part else b""
                img_path = images_dir / f"{img.id}.{ext}"
                with open(img_path, "wb") as f:
                    f.write(img_bytes)
                relative_img_path = f"{images_dir_name}/{img.id}.{ext}"
                encoded_img_path = urllib.parse.quote(relative_img_path).replace(" ", "%20")
                page_images[img.id] = encoded_img_path

        page_markdown = replace_images_in_markdown(page.markdown, page_images)
        all_markdowns.append(page_markdown)

    return separator.join(all_markdowns)

def main():
    parser = argparse.ArgumentParser(
        description="Convert PDF files to Markdown using Mistral OCR",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  pdf2md document.pdf
  pdf2md document1.pdf document2.pdf document3.pdf
  pdf2md *.pdf
  pdf2md document.pdf --no-progress
  pdf2md document.pdf -o out.md --inline-images
  pdf2md docs/*.pdf -o out_dir --page-range 1-2,5 --overwrite
        """,
    )
    
    parser.add_argument(
        "pdf_files",
        nargs="+",
        help="PDF file(s) to convert to Markdown"
    )
    
    parser.add_argument(
        "--no-progress",
        action="store_true",
        help="Disable progress bars",
    )
    parser.add_argument(
        "--api-key",
        type=str,
        help="Mistral API key (can also be set via MISTRAL_API_KEY environment variable)",
    )
    parser.add_argument(
        "--api-key-file",
        type=str,
        help="Path to a file containing the Mistral API key",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=str,
        help="Output .md file or directory. Use '-' for stdout. With multiple inputs, provide a directory.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing output files",
    )
    parser.add_argument(
        "--fail-fast",
        action="store_true",
        help="Stop on first error",
    )
    parser.add_argument(
        "--inline-images",
        action="store_true",
        help="Embed images as data URIs instead of saving files",
    )
    parser.add_argument(
        "--images-subdir",
        type=str,
        help="Directory name (relative to output .md) where images are stored (default: '<pdfname>_images')",
    )
    parser.add_argument(
        "--page-range",
        type=str,
        help="Pages to include, e.g. '1-3,5'",
    )
    parser.add_argument(
        "--separator",
        type=str,
        default="\n\n",
        help="Text inserted between pages (default: blank line)",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=3,
        help="Number of retries for network operations (default: 3)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        help="Optional timeout (seconds) for API operations",
    )

    args = parser.parse_args()

    # API key resolution: CLI > file > env
    api_key = args.api_key
    if not api_key and args.api_key_file:
        try:
            api_key = Path(args.api_key_file).read_text(encoding="utf-8").strip()
        except Exception as e:
            print(f"Error reading API key file: {e}")
            sys.exit(1)
    if not api_key:
        api_key = os.environ.get("MISTRAL_API_KEY")
    if not api_key:
        print("Error: MISTRAL_API_KEY environment variable not set")
        print("Please set it with: export MISTRAL_API_KEY='your-api-key'")
        print("Or provide it with the --api-key or --api-key-file argument")
        sys.exit(1)

    show_progress = not args.no_progress
    pdf_files = args.pdf_files
    successful_conversions = 0
    skipped_conversions = 0

    # Determine if output is stdout, file, or directory
    output_arg = args.output
    output_is_stdout = output_arg == "-"
    output_dir: Optional[Path] = None
    output_file: Optional[Path] = None

    if output_is_stdout and len(pdf_files) > 1:
        print("Error: cannot write multiple PDFs to stdout. Provide a directory with -o.")
        sys.exit(1)

    if output_arg and not output_is_stdout:
        p = Path(output_arg)
        if len(pdf_files) > 1:
            # Must be directory
            if p.exists() and not p.is_dir():
                print(f"Error: output path '{p}' is a file; for multiple inputs, provide a directory.")
                sys.exit(1)
            p.mkdir(parents=True, exist_ok=True)
            output_dir = p
        else:
            # Single input: could be file or directory
            if p.exists() and p.is_dir():
                output_dir = p
            elif p.suffix.lower() in (".md", "") or not p.exists():
                output_file = p if p.suffix.lower() == ".md" or not p.exists() else p.with_suffix(".md")
            else:
                # Fallback: treat as directory
                p.mkdir(parents=True, exist_ok=True)
                output_dir = p

    page_range = None
    try:
        page_range = parse_page_range(args.page_range)
    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)

    # Create overall progress bar with fixed position at bottom
    with tqdm(total=len(pdf_files), desc="Overall progress", disable=not show_progress, position=0, leave=True) as pbar:
        for pdf_path in pdf_files:
            try:
                src = Path(pdf_path)
                if not src.is_file():
                    raise FileNotFoundError(f"PDF file does not exist: {pdf_path}")

                # Decide output .md path and images base dir
                if output_is_stdout:
                    output_md_path = None
                    images_base_dir = src.parent  # best-effort when printing to stdout
                else:
                    if output_dir:
                        images_base_dir = output_dir
                        output_md_path = output_dir / f"{src.stem}.md"
                    elif output_file:
                        output_md_path = output_file if len(pdf_files) == 1 else None
                        images_base_dir = output_md_path.parent if output_md_path else src.parent
                    else:
                        output_md_path = src.with_suffix(".md")
                        images_base_dir = output_md_path.parent

                # Skip if exists and not overwriting
                if output_md_path and output_md_path.exists() and not args.overwrite:
                    if show_progress:
                        tqdm.write(f"⤴ Skipping (exists): {pdf_path} -> {output_md_path}")
                    else:
                        print(f"Skipping (exists): {pdf_path} -> {output_md_path}")
                    skipped_conversions += 1
                    pbar.update(1)
                    continue

                md_content = process_pdf_to_md(
                    pdf_path=str(src),
                    api_key=api_key,
                    show_progress=show_progress,
                    images_base_dir=images_base_dir,
                    images_subdir=args.images_subdir,
                    inline_images=args.inline_images,
                    page_range=page_range,
                    separator=args.separator,
                    retries=args.retries,
                    timeout=args.timeout,
                )

                # Write output
                if output_is_stdout or (output_file is not None and output_md_path is None):
                    # stdout
                    sys.stdout.write(md_content)
                    sys.stdout.flush()
                else:
                    assert output_md_path is not None
                    output_md_path.parent.mkdir(parents=True, exist_ok=True)
                    with open(output_md_path, "w", encoding="utf-8") as f:
                        f.write(md_content)

                if show_progress:
                    target = "stdout" if output_is_stdout else output_md_path
                    tqdm.write(f"✓ Conversion complete for {pdf_path}. Result saved to: {target}")
                else:
                    target = "stdout" if output_is_stdout else output_md_path
                    print(f"Conversion complete for {pdf_path}. Result saved to: {target}")

                successful_conversions += 1

            except Exception as e:
                if show_progress:
                    tqdm.write(f"✗ Error processing {pdf_path}: {e}")
                else:
                    print(f"Error processing {pdf_path}: {e}")
                if args.fail_fast:
                    sys.exit(1)
            finally:
                pbar.update(1)
                # Clear any page progress bar that might still be visible
                if show_progress:
                    tqdm.write("", end="\r\033[K")  # Clear line

    if show_progress:
        tqdm.write(f"\nCompleted {successful_conversions}/{len(pdf_files)} conversions successfully. Skipped: {skipped_conversions}.")
    else:
        print(f"\nCompleted {successful_conversions}/{len(pdf_files)} conversions successfully. Skipped: {skipped_conversions}.")

if __name__ == "__main__":
    main()
