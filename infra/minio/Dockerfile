# syntax=docker/dockerfile:1.7
# MinIO compilado desde el código fuente: la imagen minio/minio ya no está publicada en
# Docker Hub (ver docs/auto_equity_TDD.md §11.1). Se fija un commit concreto.
FROM golang:1.24-bookworm AS build
ARG MINIO_VERSION=v0.0.0-20260212201848-7aac2a2c5b7c
ENV CGO_ENABLED=0 GOFLAGS=-trimpath
RUN --mount=type=secret,id=extra_ca,required=false \
    if [ -s /run/secrets/extra_ca ]; then export SSL_CERT_FILE=/run/secrets/extra_ca; fi; \
    GOBIN=/out go install "github.com/minio/minio@${MINIO_VERSION}"

FROM python:3.12-slim
COPY --from=build /out/minio /usr/local/bin/minio
RUN useradd --create-home --uid 10001 minio && mkdir -p /data && chown minio /data
USER minio
EXPOSE 9000 9001
ENTRYPOINT ["minio"]
CMD ["server", "/data", "--address", ":9000", "--console-address", ":9001"]
