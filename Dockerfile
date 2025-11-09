FROM golang:1.25.1-alpine3.21 AS builder

ARG TARGETOS
ARG TARGETARCH
ENV CGO_ENABLED=0 GOOS=${TARGETOS} GOARCH=${TARGETARCH}

WORKDIR /app

COPY go.mod go.sum /app/
RUN go mod download

COPY . /app

# Build the controller binary with build cache
RUN --mount=type=cache,target=/root/.cache/go-build \
    --mount=type=cache,target=/go/pkg \
    go build -o mariadb-operator cmd/controller/*.go

FROM gcr.io/distroless/static AS app

WORKDIR /
COPY --from=builder /app/mariadb-operator /bin/mariadb-operator 
USER 65532:65532

ENTRYPOINT ["/bin/mariadb-operator"]
