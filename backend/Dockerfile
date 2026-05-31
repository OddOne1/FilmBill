FROM node:20-alpine

# Chromium for PDF generation via puppeteer
RUN apk add --no-cache \
    chromium nss freetype harfbuzz \
    ca-certificates ttf-freefont font-noto font-noto-extra

ENV PUPPETEER_SKIP_DOWNLOAD=true
ENV PUPPETEER_EXECUTABLE_PATH=/usr/bin/chromium-browser

WORKDIR /app
COPY package.json ./
RUN npm install --no-audit --no-fund
COPY src ./src
EXPOSE 4000
CMD ["node", "src/index.js"]
