<script setup lang="ts">
// The signed-in user's picture, or the slot (initials) when none is set or the
// file cannot be loaded. Shared by every surface that shows the account avatar.
import { computed, ref, watch } from "vue";
import { currentUser } from "../state/auth";
import { profilePictureUrl } from "../services/auth";

defineOptions({ inheritAttrs: false });
const url = computed(() =>
  currentUser.value?.profile_picture_version && currentUser.value.id !== "mock"
    ? profilePictureUrl(
        currentUser.value.id,
        currentUser.value.profile_picture_version,
      )
    : "",
);
const failed = ref(false);
watch(url, () => (failed.value = false));
</script>

<template>
  <img
    v-if="url && !failed"
    v-bind="$attrs"
    :src="url"
    alt=""
    @error="failed = true"
  />
  <slot v-else />
</template>
