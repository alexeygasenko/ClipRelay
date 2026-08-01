<script setup>
import { computed, onMounted, reactive, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";

import BrandHeader from "../components/BrandHeader.vue";
import { getJson, postForm } from "../api.js";
import { session } from "../session.js";

const route = useRoute();
const router = useRouter();
const loading = ref(true);
const saving = ref(false);
const error = ref("");
const saved = ref(false);
const user = ref(null);
const form = reactive({
  username: "",
  is_admin: false,
  is_disabled: false,
  allow_tiktok: false,
  allow_instagram: false,
  allow_twitter: false,
  allow_reddit: false,
  allow_youtube: false,
  allow_spotify: false,
});

const services = [
  ["allow_tiktok", "TikTok", "Видео, фотоподборки и мониторинг каналов."],
  ["allow_instagram", "Instagram", "Reels, видео и публикации с изображениями."],
  ["allow_twitter", "X", "Видео, изображения и текст постов."],
  ["allow_reddit", "Reddit", "Видео, галереи и текстовые публикации."],
  ["allow_youtube", "YouTube", "Превью, загрузка видео и Telegram-посты."],
  ["allow_spotify", "Spotify", "MP3 320 кбит/с через Premium cookies."],
];

async function load() {
  loading.value = true;
  error.value = "";
  try {
    const payload = await getJson(`/api/admin/users/${encodeURIComponent(route.params.id)}`);
    user.value = payload.user || payload;
    Object.keys(form).forEach((key) => {
      form[key] = key === "username" ? (user.value[key] || "") : Boolean(user.value[key]);
    });
  } catch (loadError) {
    error.value = loadError.message;
  } finally {
    loading.value = false;
  }
}

async function save() {
  if (saving.value) return;
  saving.value = true;
  saved.value = false;
  error.value = "";
  try {
    const values = { username: form.username };
    Object.entries(form).forEach(([key, value]) => {
      if (key !== "username" && value) values[key] = "on";
    });
    await postForm(`/admin/users/${route.params.id}`, values);
    await load();
    saved.value = true;
    window.setTimeout(() => {
      saved.value = false;
    }, 2800);
  } catch (saveError) {
    error.value = saveError.message;
  } finally {
    saving.value = false;
  }
}

watch(() => route.params.id, load);
onMounted(load);
</script>

<template>
  <main class="page-shell admin-page">
    <BrandHeader
      :title="user?.username || 'Пользователь'"
      lead="Роль, состояние аккаунта и разрешённые источники."
    >
      <template #actions>
        <RouterLink class="header-pill" to="/admin/users">← К списку</RouterLink>
        <RouterLink class="header-pill" to="/">На главную</RouterLink>
      </template>
    </BrandHeader>

    <div v-if="loading" class="skeleton admin-detail-skeleton"></div>

    <section v-else-if="error && !user" class="empty-state glass-panel">
      <span class="empty-state-mark" aria-hidden="true">!</span>
      <h2>Не удалось открыть пользователя</h2>
      <p>{{ error }}</p>
      <button class="button button-primary" type="button" @click="load">Повторить</button>
    </section>

    <form v-else class="admin-detail glass-panel" @submit.prevent="save">
      <div v-if="saved" class="inline-alert inline-alert-success" role="status">
        Пользователь обновлён.
      </div>
      <div v-if="error" class="inline-alert inline-alert-error" role="alert">{{ error }}</div>

      <div class="admin-profile-row">
        <span class="admin-profile-avatar" aria-hidden="true">
          {{ form.username.slice(0, 1).toUpperCase() }}
        </span>
        <div class="field-group">
          <label for="admin-username">Логин</label>
          <input id="admin-username" v-model="form.username" required>
        </div>
      </div>

      <section class="admin-permission-section">
        <div class="section-heading section-heading-compact">
          <div><h2>Роль и состояние</h2><p>Критичные права аккаунта.</p></div>
        </div>
        <div class="permission-grid permission-grid-two">
          <label class="choice-card">
            <input v-model="form.is_admin" type="checkbox" :disabled="user.id === 1">
            <span><strong>Администратор</strong><small>Может управлять пользователями и их настройками.</small></span>
          </label>
          <label class="choice-card choice-card-danger">
            <input
              v-model="form.is_disabled"
              type="checkbox"
              :disabled="user.id === session.bootstrap?.user?.id"
            >
            <span><strong>Отключить пользователя</strong><small>Вход и публикации будут заблокированы.</small></span>
          </label>
        </div>
      </section>

      <section class="admin-permission-section">
        <div class="section-heading section-heading-compact">
          <div><h2>Доступ к источникам</h2><p>Показывать только нужные сервисы на главном экране.</p></div>
        </div>
        <div class="permission-grid">
          <label v-for="[key, label, description] in services" :key="key" class="choice-card">
            <input v-model="form[key]" type="checkbox">
            <span><strong>{{ label }}</strong><small>{{ description }}</small></span>
          </label>
        </div>
      </section>

      <div class="admin-detail-actions">
        <button class="button button-primary" type="submit" :disabled="saving">
          {{ saving ? "Сохраняем…" : "Сохранить права" }}
        </button>
        <button
          class="button button-secondary"
          type="button"
          @click="router.push({ path: '/settings', query: { user_id: user.id } })"
        >
          Настройки пользователя
        </button>
      </div>
    </form>
  </main>
</template>
