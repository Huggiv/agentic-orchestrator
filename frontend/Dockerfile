FROM node:20-alpine

WORKDIR /app

COPY package*.json ./
RUN npm ci

COPY . .

ENV VITE_PROXY_TARGET=http://backend:8015

EXPOSE 5175

CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0", "--port", "5175"]