# Run: docker run --rm -i -e FACEBOOK_COOKIES='{"c_user":"...","xs":"..."}' facebook-lyr
#      or mount a host session: -v ~/.facebook-lyr:/home/pwuser/.facebook-lyr
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/home/pwuser \
    USER_DATA_DIR=/home/pwuser/.facebook-lyr/profile

WORKDIR /app

RUN useradd --create-home --shell /bin/bash pwuser

COPY pyproject.toml uv.lock ./
RUN pip install --no-cache-dir uv
RUN uv sync --frozen --no-dev

COPY facebook_mcp_server ./facebook_mcp_server
COPY README.md AGENTS.md CLAUDE.md LICENSE ./

USER pwuser

# Cookie scraping uses only outbound HTTPS to facebook.com / threads.net
EXPOSE 0

ENTRYPOINT ["uv", "run", "facebook-lyr"]
