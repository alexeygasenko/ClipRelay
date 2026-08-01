<script setup>
import { computed } from "vue";
import { RouterView, useRoute } from "vue-router";

import AccountMenu from "./components/AccountMenu.vue";
import { session } from "./session.js";

const route = useRoute();
const showAccountMenu = computed(
  () => session.bootstrap?.authenticated
    && !["login", "register", "setup-admin"].includes(route.name),
);

function reloadPage() {
  window.location.reload();
}
</script>

<template>
  <div class="app-background" aria-hidden="true">
    <span class="ambient ambient-one"></span>
    <span class="ambient ambient-two"></span>
    <span class="ambient ambient-three"></span>
    <span class="background-lines"></span>
  </div>

  <AccountMenu v-if="showAccountMenu" />

  <div
    v-if="session.loading && !session.bootstrap"
    class="app-loading"
    role="status"
    aria-live="polite"
  >
    <div class="loading-mark" aria-hidden="true">
      <span></span><span></span><span></span>
    </div>
    <strong>ClipRelay</strong>
    <small>Загружаем рабочее пространство</small>
  </div>

  <div
    v-else-if="session.error && !session.bootstrap"
    class="fatal-state glass-panel"
  >
    <div class="brand-title brand-title-compact">ClipRelay</div>
    <h1>Не удалось открыть приложение</h1>
    <p>{{ session.error }}</p>
    <button type="button" @click="reloadPage">
      Попробовать снова
    </button>
  </div>

  <RouterView v-else v-slot="{ Component, route: currentRoute }">
    <Transition name="route-scene" mode="out-in">
      <component
        :is="Component"
        :key="currentRoute.name === 'home' ? 'home' : currentRoute.fullPath"
      />
    </Transition>
  </RouterView>
</template>
