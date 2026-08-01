import { createRouter, createWebHistory } from "vue-router";

import { loadBootstrap, session } from "./session.js";

const HomeView = () => import("./views/HomeView.vue");
const SettingsView = () => import("./views/SettingsView.vue");
const EditorView = () => import("./views/EditorView.vue");
const AuthView = () => import("./views/AuthView.vue");
const AdminUsersView = () => import("./views/AdminUsersView.vue");
const AdminUserView = () => import("./views/AdminUserView.vue");
const DoneView = () => import("./views/DoneView.vue");

const routes = [
  { path: "/", name: "home", component: HomeView, meta: { depth: 0 } },
  {
    path: "/settings",
    name: "settings",
    component: SettingsView,
    meta: { depth: 1 },
  },
  {
    path: "/media/post/:id",
    name: "media-editor",
    component: EditorView,
    props: { kind: "media" },
    meta: { depth: 2 },
  },
  {
    path: "/youtube/post/:id",
    name: "youtube-editor",
    component: EditorView,
    props: { kind: "youtube" },
    meta: { depth: 2 },
  },
  {
    path: "/spotify/post/:id",
    name: "spotify-editor",
    component: EditorView,
    props: { kind: "spotify" },
    meta: { depth: 2 },
  },
  {
    path: "/login",
    name: "login",
    component: AuthView,
    props: { mode: "login" },
    meta: { public: true, depth: 0 },
  },
  {
    path: "/register",
    name: "register",
    component: AuthView,
    props: { mode: "register" },
    meta: { public: true, depth: 1 },
  },
  {
    path: "/setup-admin",
    name: "setup-admin",
    component: AuthView,
    props: { mode: "setup" },
    meta: { public: true, depth: 1 },
  },
  {
    path: "/admin/users",
    name: "admin-users",
    component: AdminUsersView,
    meta: { admin: true, depth: 1 },
  },
  {
    path: "/admin/users/:id",
    name: "admin-user",
    component: AdminUserView,
    meta: { admin: true, depth: 2 },
  },
  {
    path: "/done",
    name: "done",
    component: DoneView,
    meta: { depth: 1 },
  },
  { path: "/:pathMatch(.*)*", redirect: "/" },
];

const router = createRouter({
  history: createWebHistory(),
  routes,
  scrollBehavior(_to, _from, savedPosition) {
    return savedPosition || { top: 0 };
  },
});

router.beforeEach(async (to) => {
  let bootstrap;
  try {
    bootstrap = await loadBootstrap();
  } catch {
    return true;
  }

  if (bootstrap.setup_required && to.name !== "setup-admin") {
    return { name: "setup-admin" };
  }
  if (
    bootstrap.auth_supported
    && !bootstrap.authenticated
    && !to.meta.public
  ) {
    return {
      name: "login",
      query: { next: to.fullPath !== "/" ? to.fullPath : undefined },
    };
  }
  if (
    bootstrap.authenticated
    && !bootstrap.setup_required
    && ["login", "register", "setup-admin"].includes(to.name)
  ) {
    return { name: "home" };
  }
  if (to.meta.admin && !bootstrap.user?.is_admin) {
    return { name: "home" };
  }
  session.error = "";
  return true;
});

export default router;
