# PDF to Markdown Converter

This tool converts PDF files to Markdown format using Mistral OCR API, preserving images and text structure.

## Installation

Install the package using pip:

```bash
pip install pdf2md
```

## Setup

You need to set your Mistral API key to use this tool. You can do this in two ways:

1. Set it as an environment variable:
```bash
export MISTRAL_API_KEY=your-api-key-here
```

2. Pass it directly as a command-line argument:
```bash
pdf2md --api-key your-api-key-here document.pdf
```

## Usage

Basic usage:
```bash
pdf2md document.pdf
```

Convert multiple files:
```bash
pdf2md document1.pdf document2.pdf document3.pdf
```

Convert all PDF files in a directory:
```bash
pdf2md *.pdf
```

Disable progress bars:
```bash
pdf2md document.pdf --no-progress
```

## Features

- Converts PDF documents to Markdown format
- Extracts and saves images from PDF files
- Provides progress bars for better user feedback
- Handles multiple files in a single command
- Robust error handling that continues processing other files even if one fails
- URL-encoded image paths for proper Markdown rendering

## Output

For each input PDF file, the tool will create:
1. A Markdown file with the same name (but .md extension)
2. A directory containing extracted images, named after the PDF file with `_images` suffix

For example, for `document.pdf`, the output will be:
- `document.md` - the main Markdown file
- `document_images/` - directory containing all extracted images