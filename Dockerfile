# syntax=docker/dockerfile:1.7
FROM eclipse-temurin:17-jre AS java-runtime

FROM python:3.11-slim-bookworm AS python-dependencies

WORKDIR /build
COPY requirements-container.lock ./
COPY wheelhouse /wheelhouse
RUN --mount=type=cache,id=linkparse-pip,target=/root/.cache/pip,sharing=locked \
    pip install --prefix=/install --no-index --find-links=/wheelhouse --no-deps \
    -r requirements-container.lock

FROM python:3.11-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    JAVA_HOME=/opt/java/openjdk \
    PATH="/opt/java/openjdk/bin:${PATH}"

COPY --from=java-runtime /opt/java/openjdk /opt/java/openjdk
COPY --from=python-dependencies /install /usr/local
RUN java -version

WORKDIR /app
COPY app ./app

RUN useradd --create-home --uid 10001 linkparse \
    && mkdir -p /app/data/uploads /app/data/jobs /app/data/results /app/data/tmp \
    && chown -R linkparse:linkparse /app
USER linkparse

EXPOSE 8000
CMD ["gunicorn", "app.main:app", "-k", "uvicorn.workers.UvicornWorker", "-w", "1", "-b", "0.0.0.0:8000", "--timeout", "300"]

FROM runtime AS test

USER root
RUN --mount=type=cache,id=linkparse-pip,target=/root/.cache/pip,sharing=locked \
    pip install "httpx>=0.28,<1" "pytest>=8,<9"
COPY tests ./tests
RUN chown -R linkparse:linkparse /app/tests
USER linkparse
RUN python -m pytest

FROM runtime AS production
