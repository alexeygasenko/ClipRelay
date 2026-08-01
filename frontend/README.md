# ClipRelay frontend

Vue 3 SPA для основного интерфейса ClipRelay. В production Vite собирает
статические файлы в `app/static/frontend`.

## Локальная разработка

1. Запустите backend на `http://127.0.0.1:8080`.
2. Установите зависимости: `npm install`.
3. Запустите Vite: `npm run dev`.

Если backend работает на другом адресе, задайте `VITE_BACKEND_URL` перед
запуском Vite.

## Production-сборка

```sh
npm ci
npm run build
```

Dockerfile выполняет эту сборку автоматически в отдельном Node.js stage.
