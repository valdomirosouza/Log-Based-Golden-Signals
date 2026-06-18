FROM golang:1.26-alpine AS builder
# GOTOOLCHAIN=auto lets the Go command self-fetch the toolchain declared in go.mod's
# `toolchain` directive if the base image bundles an older patch release. This keeps the
# build resilient to the floating `golang:1.26-alpine` tag drifting to a Go patch level
# below what go.mod requires (issue #49).
ENV GOTOOLCHAIN=auto
WORKDIR /app
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 GOOS=linux go build -trimpath -ldflags="-s -w" -o /event-worker ./cmd/event-worker

FROM gcr.io/distroless/static-debian12:nonroot
COPY --from=builder /event-worker /event-worker
EXPOSE 9091
ENTRYPOINT ["/event-worker"]
