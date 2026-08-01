<script setup>
import { computed, reactive, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";

import { pathFromUrl, postForm } from "../api.js";
import { clearBootstrap, loadBootstrap, session } from "../session.js";

const props = defineProps({
  mode: { type: String, required: true },
});

const route = useRoute();
const router = useRouter();
const busy = ref(false);
const error = ref("");
const revealPassword = ref(false);
const form = reactive({
  username: "",
  password: "",
  confirm_password: "",
  setup_username: "",
  setup_password: "",
});

const isLogin = computed(() => props.mode === "login");
const isRegister = computed(() => props.mode === "register");
const isSetup = computed(() => props.mode === "setup");
const setupCredentialsRequired = computed(
  () => session.bootstrap?.setup_credentials_required !== false,
);
const title = computed(() => ({
  login: "С возвращением",
  register: "Создайте аккаунт",
  setup: "Первичная настройка",
})[props.mode]);
const lead = computed(() => ({
  login: "Войдите, чтобы продолжить работу с публикациями.",
  register: "Настройки каналов и сервисов будут храниться отдельно.",
  setup: "Подтвердите данные из config.yaml и задайте пароль администратора.",
})[props.mode]);
const endpoint = computed(() => ({
  login: "/login",
  register: "/register",
  setup: "/setup-admin",
})[props.mode]);

function safeNext(value) {
  if (typeof value !== "string" || !value.startsWith("/") || value.startsWith("//")) return "/";
  return value;
}

async function submit() {
  if (busy.value) return;
  error.value = "";
  if (!isLogin.value && form.password !== form.confirm_password) {
    error.value = "Пароли не совпадают.";
    return;
  }
  busy.value = true;
  try {
    const values = {
      username: form.username,
      password: form.password,
      confirm_password: form.confirm_password,
      next: safeNext(route.query.next),
    };
    if (isSetup.value) {
      values.setup_username = form.setup_username;
      values.setup_password = form.setup_password;
    }
    const response = await postForm(endpoint.value, values);
    clearBootstrap();
    await loadBootstrap(true);
    const fallback = isLogin.value ? safeNext(route.query.next) : "/";
    await router.replace(pathFromUrl(response.redirect, fallback));
  } catch (submitError) {
    error.value = submitError.message;
  } finally {
    busy.value = false;
  }
}

watch(
  () => props.mode,
  () => {
    error.value = "";
    form.password = "";
    form.confirm_password = "";
  },
);
</script>

<template>
  <main class="auth-shell">
    <section class="auth-brand-panel">
      <RouterLink class="brand-title auth-brand" to="/">ClipRelay</RouterLink>
      <div class="auth-value-copy">
        <span class="section-kicker">Медиа → Telegram</span>
        <h1>Публикуйте без лишних шагов.</h1>
        <p>Видео, изображения, музыка и текст из шести источников в одном рабочем пространстве.</p>
      </div>
    </section>

    <section class="auth-form-panel">
      <form class="auth-card glass-panel" @submit.prevent="submit">
        <div class="auth-heading">
          <span class="section-kicker">ClipRelay account</span>
          <h2>{{ title }}</h2>
          <p>{{ lead }}</p>
        </div>

        <div v-if="error" class="inline-alert inline-alert-error" role="alert">
          {{ error }}
        </div>

        <fieldset
          v-if="isSetup && setupCredentialsRequired"
          class="setup-credentials"
        >
          <legend>Данные первичной настройки из config.yaml</legend>
          <p>Они используются только для подтверждения первого администратора.</p>
          <div class="field-group">
            <label for="setup-username">Логин из config.yaml</label>
            <input
              id="setup-username"
              v-model="form.setup_username"
              autocomplete="username"
              required
              autofocus
            >
          </div>
          <div class="field-group">
            <label for="setup-password">Пароль из config.yaml</label>
            <input
              id="setup-password"
              v-model="form.setup_password"
              type="password"
              autocomplete="current-password"
              required
            >
          </div>
        </fieldset>

        <div v-if="!isSetup" class="field-group">
          <label for="auth-username">Логин</label>
          <input
            id="auth-username"
            v-model="form.username"
            autocomplete="username"
            minlength="3"
            maxlength="32"
            required
            autofocus
            placeholder="Введите логин"
          >
          <p v-if="isRegister" class="field-help">
            3–32 символа: латиница, цифры, точка, дефис или подчёркивание.
          </p>
        </div>

        <div class="field-group">
          <label for="auth-password">{{ isSetup ? "Новый пароль администратора" : "Пароль" }}</label>
          <div class="secret-field">
            <input
              id="auth-password"
              v-model="form.password"
              :type="revealPassword ? 'text' : 'password'"
              :autocomplete="isLogin ? 'current-password' : 'new-password'"
              :minlength="isLogin ? undefined : 8"
              required
            >
            <button
              type="button"
              :aria-label="revealPassword ? 'Скрыть пароль' : 'Показать пароль'"
              @click="revealPassword = !revealPassword"
            >
              {{ revealPassword ? "Скрыть" : "Показать" }}
            </button>
          </div>
        </div>

        <div v-if="!isLogin" class="field-group">
          <label for="auth-confirm">Повторите пароль</label>
          <input
            id="auth-confirm"
            v-model="form.confirm_password"
            type="password"
            autocomplete="new-password"
            minlength="8"
            required
          >
        </div>

        <button class="button button-primary auth-submit" type="submit" :disabled="busy">
          <span v-if="busy" class="button-spinner" aria-hidden="true"></span>
          {{
            busy
              ? "Проверяем…"
              : isLogin
                ? "Войти"
                : isRegister
                  ? "Создать аккаунт"
                  : "Завершить настройку"
          }}
        </button>

        <p v-if="isLogin" class="auth-switch">
          Нет аккаунта?
          <RouterLink to="/register">Зарегистрироваться</RouterLink>
        </p>
        <p v-else-if="isRegister" class="auth-switch">
          Уже есть аккаунт?
          <RouterLink to="/login">Войти</RouterLink>
        </p>
      </form>
    </section>
  </main>
</template>
