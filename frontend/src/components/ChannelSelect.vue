<script setup>
import { computed, watch } from "vue";

const props = defineProps({
  modelValue: { type: String, default: "" },
  channels: { type: Array, default: () => [] },
  label: { type: String, default: "Telegram-канал или чат" },
  id: { type: String, required: true },
});

const emit = defineEmits(["update:modelValue"]);

const currentValue = computed(() => {
  if (props.channels.some((channel) => channel.chat_id === props.modelValue)) {
    return props.modelValue;
  }
  return props.channels[0]?.chat_id || "";
});

watch(
  currentValue,
  (value) => {
    if (value !== props.modelValue) emit("update:modelValue", value);
  },
  { immediate: true },
);

function safeChannelLabel(channel) {
  const name = String(channel.name || "").trim();
  const chatId = String(channel.chat_id || "").trim();
  const normalizedName = name.replace(/^@/, "").toLowerCase();
  const normalizedId = chatId.replace(/^@/, "").toLowerCase();
  const exposesIdentifier = (
    !name
    || /^-?\d+$/.test(name)
    || normalizedName === normalizedId
  );
  if (!exposesIdentifier) return name;
  return ["group", "supergroup", "private"].includes(channel.destination_type)
    ? "Telegram-чат"
    : "Telegram-канал";
}

const channelOptions = computed(() => {
  const items = props.channels.map((channel) => ({
    ...channel,
    baseLabel: safeChannelLabel(channel),
  }));
  const totals = items.reduce((counts, channel) => {
    counts.set(channel.baseLabel, (counts.get(channel.baseLabel) || 0) + 1);
    return counts;
  }, new Map());
  const seen = new Map();
  return items.map((channel) => {
    const number = (seen.get(channel.baseLabel) || 0) + 1;
    seen.set(channel.baseLabel, number);
    return {
      ...channel,
      optionLabel: totals.get(channel.baseLabel) > 1
        ? `${channel.baseLabel} · №${number}`
        : channel.baseLabel,
    };
  });
});
</script>

<template>
  <div class="field-group channel-field">
    <label :for="id">{{ label }}</label>
    <div v-if="channels.length" class="select-shell">
      <select
        :id="id"
        :value="currentValue"
        @change="emit('update:modelValue', $event.target.value)"
      >
        <option
          v-for="channel in channelOptions"
          :key="channel.chat_id"
          :value="channel.chat_id"
        >
          {{ channel.optionLabel }}
        </option>
      </select>
      <span class="select-chevron" aria-hidden="true"></span>
    </div>
    <div v-else class="empty-channel-state">
      <div>
        <strong>Канал пока не настроен</strong>
        <small>Добавьте бота и выберите канал в настройках.</small>
      </div>
      <RouterLink class="text-action" to="/settings">Настроить</RouterLink>
    </div>
  </div>
</template>
