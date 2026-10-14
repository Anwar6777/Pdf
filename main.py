import io
import os
from urllib.parse import urlparse
from ipaddress import ip_address
import socket

import requests
from PIL import Image, ImageOps, UnidentifiedImageError
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, HttpUrl, Field

app = FastAPI(title="Images to PDF API")

API_KEY = "a8f3c912e7d64b10f5a2c8e9d1b7036a4f8c2e6b9d0a1f5c7e3b6d8a2c4f9017"
MAX_IMAGES = 30
MAX_IMAGE_BYTES = 15 * 1024 * 1024

class PDFRequest(BaseModel):
    image_urls: list[HttpUrl] = Field(min_length=1, max_length=MAX_IMAGES)
    page_size: str = "A4"  # A4 or image

def authenticate(x_api_key: str | None):
    if not x_api_key or x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")

def download_image(url: str) -> Image.Image:
    parsed = urlparse(url)

    if parsed.scheme != "https" or not parsed.hostname:
        raise HTTPException(400, detail="Only HTTPS image URLs are accepted")

    # Prevent requests to private/internal network addresses.
    try:
        addresses = socket.getaddrinfo(
            parsed.hostname, 443, type=socket.SOCK_STREAM
        )
        for address in addresses:
            ip = ip_address(address[4][0])
            if not ip.is_global:
                raise HTTPException(400, detail="Private network URLs are not allowed")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(400, detail="Could not validate image host")

    try:
        with requests.get(
            url,
            timeout=(5, 20),
            stream=True,
            headers={"User-Agent": "ImagesToPDF/1.0"},
            allow_redirects=False,
        ) as response:
            response.raise_for_status()

            content_type = response.headers.get("Content-Type", "")
            if not content_type.lower().startswith("image/"):
                raise HTTPException(400, detail="URL did not return an image")

            data = bytearray()
            for chunk in response.iter_content(64 * 1024):
                data.extend(chunk)
                if len(data) > MAX_IMAGE_BYTES:
                    raise HTTPException(413, detail="Image exceeds 15 MB limit")

        image = Image.open(io.BytesIO(data))
        image.load()
        return ImageOps.exif_transpose(image).convert("RGB")

    except HTTPException:
        raise
    except (requests.RequestException, UnidentifiedImageError, OSError):
        raise HTTPException(422, detail="Could not download or decode an image")

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/api/convert")
def convert_to_pdf(
    body: PDFRequest,
    x_api_key: str | None = Header(default=None),
):
    authenticate(x_api_key)

    if body.page_size not in ("A4", "image"):
        raise HTTPException(400, detail="page_size must be A4 or image")

    buffer = io.BytesIO()

    try:
        pdf = canvas.Canvas(buffer, pagesize=(595.27, 841.89))

        for url in body.image_urls:
            image = download_image(str(url))

            if body.page_size == "A4":
                page_w, page_h = 595.27, 841.89
            else:
                page_w, page_h = image.width, image.height

            pdf.setPageSize((page_w, page_h))

            margin = 24 if body.page_size == "A4" else 0
            scale = min(
                (page_w - 2 * margin) / image.width,
                (page_h - 2 * margin) / image.height,
            )

            draw_w = image.width * scale
            draw_h = image.height * scale
            x = (page_w - draw_w) / 2
            y = (page_h - draw_h) / 2

            pdf.drawImage(
                ImageReader(image),
                x, y,
                width=draw_w,
                height=draw_h,
                preserveAspectRatio=True,
            )
            pdf.showPage()

        pdf.save()
        buffer.seek(0)

    except HTTPException:
        raise
    except Exception:
        raise HTTPException(500, detail="PDF generation failed")

    return StreamingResponse(
        buffer,
        media_type="application/pdf",
        headers={
            "Content-Disposition": 'attachment; filename="images.pdf"'
        },
    )
