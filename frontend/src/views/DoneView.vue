<script setup>
import { computed } from "vue";
import { useRoute } from "vue-router";

const route = useRoute();
const source = computed(() => route.query.source || "media");
const description = computed(() => ({
  youtube: "Telegram принял превью, ссылку и текст публикации.",
  spotify: "Telegram принял MP3-трек и подготовленную подпись.",
  media: "Telegram принял медиа и подготовленную подпись.",
  tiktok: "Telegram принял медиа и подготовленную подпись.",
  instagram: "Telegram принял медиа и подготовленную подпись.",
  twitter: "Telegram принял публикацию из X.",
  reddit: "Telegram принял публикацию Reddit.",
})[source.value] || "Публикация отправлена в Telegram.");
</script>

<template>
  <main class="completion-shell">
    <section class="completion-card glass-panel">
      <div class="completion-mark" aria-hidden="true"><span>✓</span></div>
      <span class="section-kicker">Готово</span>
      <h1>Пост опубликован</h1>
      <p>{{ description }}</p>
      <RouterLink class="button button-primary" to="/">
        Подготовить следующий пост
      </RouterLink>
    </section>
  </main>
</template>
