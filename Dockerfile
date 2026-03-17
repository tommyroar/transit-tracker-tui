FROM ghcr.io/astral-sh/uv:python3.13-bookworm

WORKDIR /app

# Copy project files
COPY pyproject.toml .
COPY src/ src/
COPY docker_entrypoint.py .

# Patch pyproject.toml for Linux/Docker:
# - Relax Python requirement from >=3.14 to >=3.13
# - Remove macOS-only and hardware deps
RUN sed -i 's/requires-python = ">=3.14"/requires-python = ">=3.13"/' pyproject.toml && \
    sed -i '/"rumps>=/d' pyproject.toml && \
    sed -i '/"pyobjc-framework-quartz>=/d' pyproject.toml && \
    sed -i '/"esptool>=/d' pyproject.toml && \
    sed -i '/"opencv-python>=/d' pyproject.toml && \
    sed -i '/"opencv-python-headless>=/d' pyproject.toml && \
    sed -i '/"bdfparser>=/d' pyproject.toml && \
    sed -i '/"numpy>=/d' pyproject.toml && \
    sed -i '/"pillow>=/d' pyproject.toml && \
    sed -i '/"pyserial>=/d' pyproject.toml && \
    sed -i '/"questionary>=/d' pyproject.toml && \
    sed -i '/"rich>=/d' pyproject.toml

# Install dependencies
RUN uv sync --no-dev

EXPOSE 8000

CMD ["uv", "run", "python", "docker_entrypoint.py"]
