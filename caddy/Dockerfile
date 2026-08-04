ARG GOPROXY=https://goproxy.cn,direct
ARG GOSUMDB=sum.golang.google.cn

FROM caddy:2-builder AS builder

ARG GOPROXY
ARG GOSUMDB

ENV GOPROXY=${GOPROXY} \
    GOSUMDB=${GOSUMDB}

RUN xcaddy build \
    --with github.com/caddy-dns/cloudflare

FROM caddy:2

COPY --from=builder /usr/bin/caddy /usr/bin/caddy
