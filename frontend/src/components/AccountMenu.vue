<script setup>
import { nextTick, onBeforeUnmount, ref } from "vue";
import { useRouter } from "vue-router";

import { postForm } from "../api.js";
import { clearBootstrap, loadBootstrap, session } from "../session.js";

const router = useRouter();
const open = ref(false);
const panel = ref(null);
const busy = ref(false);

function setOpen(value) {
  open.value = value;
  document.body.classList.toggle("account-menu-open", value);
  if (value) {
    nextTick(() => panel.value?.querySelector("a, button")?.focus());
  }
}

async function logout() {
  if (busy.value) return;
  busy.value = true;
  try {
    await postForm("/logout");
    clearBootstrap();
    await loadBootstrap(true);
    setOpen(false);
    await router.replace({ name: "login" });
  } finally {
    busy.value = false;
  }
}

function onKeydown(event) {
  if (event.key === "Escape" && open.value) setOpen(false);
}

document.addEventListener("keydown", onKeydown);
onBeforeUnmount(() => {
  document.removeEventListener("keydown", onKeydown);
  document.body.classList.remove("account-menu-open");
});
</script>

<template>
  <button
    class="account-trigger"
    type="button"
    aria-label="Открыть меню аккаунта"
    aria-controls="account-menu"
    :aria-expanded="open"
    @click="setOpen(!open)"
  >
    <span></span><span></span><span></span>
  </button>

  <Transition name="overlay-fade">
    <button
      v-if="open"
      class="account-overlay"
      type="button"
      aria-label="Закрыть меню аккаунта"
      @click="setOpen(false)"
    ></button>
  </Transition>

  <Transition name="drawer-slide">
    <aside
      v-if="open"
      id="account-menu"
      ref="panel"
      class="account-drawer"
      aria-label="Меню аккаунта"
    >
      <div class="account-heading">
        <span class="account-avatar" aria-hidden="true">
          {{ (session.bootstrap?.user?.username || "C").slice(0, 1).toUpperCase() }}
        </span>
        <div>
          <strong>{{ session.bootstrap?.user?.username }}</strong>
          <small>{{ session.bootstrap?.user?.is_admin ? "Администратор" : "Аккаунт ClipRelay" }}</small>
        </div>
      </div>

      <nav class="account-links">
        <RouterLink to="/" @click="setOpen(false)">Главная</RouterLink>
        <RouterLink to="/settings" @click="setOpen(false)">Настройки</RouterLink>
        <RouterLink
          v-if="session.bootstrap?.user?.is_admin"
          to="/admin/users"
          @click="setOpen(false)"
        >
          Пользователи
        </RouterLink>
      </nav>

      <button
        class="account-logout"
        type="button"
        :disabled="busy"
        @click="logout"
      >
        {{ busy ? "Выходим…" : "Выйти" }}
      </button>
    </aside>
  </Transition>
</template>
