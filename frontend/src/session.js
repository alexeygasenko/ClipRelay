import { reactive } from "vue";

import { getJson } from "./api.js";

export const session = reactive({
  bootstrap: null,
  loading: false,
  error: "",
  lastSource: "",
});

let bootstrapPromise = null;

export async function loadBootstrap(force = false) {
  if (session.bootstrap && !force) return session.bootstrap;
  if (bootstrapPromise && !force) return bootstrapPromise;
  session.loading = true;
  session.error = "";
  bootstrapPromise = getJson("/api/bootstrap")
    .then((payload) => {
      session.bootstrap = payload;
      return payload;
    })
    .catch((error) => {
      session.error = error.message;
      throw error;
    })
    .finally(() => {
      session.loading = false;
      bootstrapPromise = null;
    });
  return bootstrapPromise;
}

export function clearBootstrap() {
  session.bootstrap = null;
}

export function availableChannels() {
  return session.bootstrap?.telegram_channels || [];
}
