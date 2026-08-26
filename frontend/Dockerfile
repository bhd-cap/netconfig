# Build stage: compile the app to static assets.
FROM node:20-alpine AS builder

WORKDIR /app

# Copy manifests first so dependency installation is cached independently of
# source changes.
COPY package.json package-lock.json* ./

# Use the lockfile when present for a reproducible install.
RUN if [ -f package-lock.json ]; then \
        npm ci --no-audit --no-fund; \
    else \
        npm install --no-audit --no-fund; \
    fi

COPY . .

# Baked into the bundle at build time.
ARG VITE_API_URL=http://localhost:8000/api/v1
ENV VITE_API_URL=$VITE_API_URL

RUN npm run build


# Runtime stage: nginx serving the built assets.
#
# The previous image ran `vite` (the development server) as its production
# command, which kept the whole toolchain, node_modules and a file watcher
# resident - hundreds of MB of RSS and continuous CPU for polling - to serve
# files that never change after the build. This stage carries only the
# compiled output.
FROM nginx:alpine AS runtime

RUN rm -rf /usr/share/nginx/html/*
COPY --from=builder /app/dist /usr/share/nginx/html
COPY docker/default.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD wget -q --spider http://localhost/ || exit 1

CMD ["nginx", "-g", "daemon off;"]
