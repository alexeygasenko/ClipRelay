<script setup>
import { computed, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";

import BrandHeader from "../components/BrandHeader.vue";
import { getJson, postForm } from "../api.js";
import { session } from "../session.js";

const route = useRoute();
const router = useRouter();
const loading = ref(true);
const error = ref("");
const payload = ref(null);
const busyId = ref(null);

const page = computed(() => Math.max(1, Number(route.query.page) || 1));
const users = computed(() => payload.value?.users || []);
const pages = computed(() => payload.value?.pages || 1);
const total = computed(() => payload.value?.total ?? users.value.length);

const serviceLabels = [
  ["allow_tiktok", "TikTok"],
  ["allow_instagram", "Instagram"],
  ["allow_twitter", "X"],
  ["allow_reddit", "Reddit"],
  ["allow_youtube", "YouTube"],
  ["allow_spotify", "Spotify"],
];

async function load() {
  loading.value = true;
  error.value = "";
  try {
    payload.value = await getJson(`/api/admin/users?page=${page.value}`);
  } catch (loadError) {
    error.value = loadError.message;
  } finally {
    loading.value = false;
  }
}

async function toggleUser(user) {
  if (busyId.value) return;
  busyId.value = user.id;
  try {
    await postForm(`/admin/users/${user.id}/toggle-disabled?page=${page.value}`);
    await load();
  } catch (toggleError) {
    error.value = toggleError.message;
  } finally {
    busyId.value = null;
  }
}

watch(page, load);
onMounted(load);
</script>

<template>
  <main class="page-shell admin-page">
    <BrandHeader title="Пользователи" lead="Аккаунты, роли и доступ к источникам.">
      <template #actions>
        <RouterLink class="header-pill" to="/">На главную</RouterLink>
      </template>
    </BrandHeader>

    <section class="admin-card glass-panel">
      <div class="admin-summary">
        <div><strong>{{ total }}</strong><span>аккаунтов</span></div>
        <div><strong>{{ users.filter((user) => !user.is_disabled).length }}</strong><span>активны на странице</span></div>
      </div>

      <div v-if="error" class="inline-alert inline-alert-error" role="alert">{{ error }}</div>

      <div v-if="loading" class="admin-list">
        <div v-for="index in 5" :key="index" class="skeleton admin-row-skeleton"></div>
      </div>

      <div v-else class="admin-list">
        <article
          v-for="user in users"
          :key="user.id"
          class="admin-user-row"
          :class="{ 'admin-user-disabled': user.is_disabled }"
        >
          <button
            class="admin-user-main"
            type="button"
            @click="router.push(`/admin/users/${user.id}`)"
          >
            <span class="destination-avatar" aria-hidden="true">
              {{ user.username.slice(0, 1).toUpperCase() }}
            </span>
            <span>
              <strong>{{ user.username }}</strong>
              <small>
                #{{ user.id }}
                <template v-if="user.is_admin"> · администратор</template>
                <template v-if="user.is_disabled"> · отключён</template>
              </small>
            </span>
          </button>

          <div class="admin-service-badges" aria-label="Доступные источники">
            <span
              v-for="[key, label] in serviceLabels.filter(([key]) => user[key])"
              :key="key"
            >{{ label }}</span>
            <small v-if="!serviceLabels.some(([key]) => user[key])">Нет источников</small>
          </div>

          <button
            class="button button-secondary admin-toggle"
            type="button"
            :disabled="busyId === user.id || user.id === session?.bootstrap?.user?.id"
            @click="toggleUser(user)"
          >
            {{ busyId === user.id ? "Сохраняем…" : user.is_disabled ? "Включить" : "Отключить" }}
          </button>
        </article>
      </div>

      <nav class="pagination" aria-label="Страницы пользователей">
        <button
          class="button button-secondary"
          type="button"
          :disabled="page <= 1"
          @click="router.push({ query: { page: page - 1 } })"
        >← Назад</button>
        <span>Страница <strong>{{ page }}</strong> из {{ pages }}</span>
        <button
          class="button button-secondary"
          type="button"
          :disabled="page >= pages"
          @click="router.push({ query: { page: page + 1 } })"
        >Дальше →</button>
      </nav>
    </section>
  </main>
</template>
