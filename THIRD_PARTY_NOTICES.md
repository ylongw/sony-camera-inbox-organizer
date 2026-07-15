# Third-Party Notices

The application source is MIT licensed. The container installs separate
programs and Python packages under their own terms.

| Component | Use | License/source |
| --- | --- | --- |
| FFmpeg | Decode, frame extraction, H.264/AAC encoding | LGPL/GPL depending on the Debian build; `apt source ffmpeg` |
| ExifTool | Read media dates and write JPEG metadata | Artistic License 1.0 or GPL-1.0-or-later; `apt source libimage-exiftool-perl` |
| FastAPI / Starlette | Web API | MIT / BSD-3-Clause |
| Uvicorn | ASGI server | BSD-3-Clause |
| Pydantic | Configuration validation | MIT |
| Jinja2 | HTML templates | BSD-3-Clause |
| ruamel.yaml | YAML parsing and round-trip writing | MIT |

Debian package copyright files remain installed in `/usr/share/doc` in the
image. Python package metadata remains in the installed distributions. Image
publishers should retain those files and provide the corresponding source as
required by the exact package versions they distribute.
