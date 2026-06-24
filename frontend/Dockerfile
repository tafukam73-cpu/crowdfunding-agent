FROM node:20-slim

WORKDIR /app

# 依存関係を先にインストール（キャッシュ活用）
COPY package.json ./
RUN npm install

# アプリ本体
COPY . .

EXPOSE 3000

# Step 1 は開発モードで起動（ホットリロード可）
CMD ["npm", "run", "dev"]
