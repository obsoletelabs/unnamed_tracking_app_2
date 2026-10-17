<script lang="ts">
// Modals can close in any order (a page may unmount beneath a confirm), so
// only the first to open saves the page's overflow and the last restores it.
let openModals = 0;
let savedOverflow = "";
</script>

<script setup lang="ts">
import { ref, onMounted, onBeforeUnmount, useId } from "vue";
import AppIcon from "./AppIcon.vue";
import { containModalTab } from "../services/focus";
defineOptions({ inheritAttrs: false });

const props = withDefaults(
  defineProps<{
    title: string;
    description?: string;
    dismissible?: boolean;
    size?: "standard" | "wide" | "editor";
    // A fixed-height dialog whose content manages its own scrolling, so tabs
    // and actions stay put while only the editor body scrolls.
    bounded?: boolean;
  }>(),
  { dismissible: true, size: "standard", bounded: false },
);
const emit = defineEmits<{ close: [] }>();
const dialog = ref<HTMLDialogElement | null>(null);
const titleId = useId();
let previousFocus: HTMLElement | null = null;
let active = false;
function dismiss() {
  if (props.dismissible) emit("close");
}
// Browsers may close a <dialog> despite a prevented cancel (e.g. a repeated
// Escape); keep the component and its parent in step with what's on screen.
function closed() {
  if (!active || !dialog.value || dialog.value.open) return;
  if (props.dismissible) emit("close");
  else dialog.value.showModal();
}
function backdrop(event: MouseEvent) {
  if (event.target !== dialog.value || !dialog.value) return;
  const bounds = dialog.value.getBoundingClientRect();
  if (
    event.clientX < bounds.left ||
    event.clientX > bounds.right ||
    event.clientY < bounds.top ||
    event.clientY > bounds.bottom
  )
    dismiss();
}
onMounted(() => {
  previousFocus =
    document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null;
  if (openModals++ === 0) {
    savedOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
  }
  active = true;
  dialog.value?.showModal();
});
onBeforeUnmount(() => {
  active = false;
  dialog.value?.close();
  if (--openModals === 0) document.body.style.overflow = savedOverflow;
  if (previousFocus?.isConnected) previousFocus.focus({ preventScroll: true });
});
</script>

<template>
  <Teleport to="body">
    <dialog
      v-bind="$attrs"
      ref="dialog"
      class="ui-modal"
      :class="{
        'ui-modal-wide': size === 'wide',
        'ui-modal-editor': size === 'editor',
        'ui-modal-bounded': bounded,
      }"
      :aria-labelledby="titleId"
      @cancel.prevent="dismiss"
      @close="closed"
      @click="backdrop"
      @keydown="containModalTab($event, dialog)"
    >
      <header
        class="modal-header"
        :class="{ 'modal-header-hero': $slots.header }"
      >
        <!-- A replacement header must label the dialog with titleId. -->
        <slot name="header" :title-id="titleId">
          <div>
            <h2 :id="titleId">{{ title }}</h2>
            <p v-if="description">{{ description }}</p>
          </div>
        </slot>
        <button
          v-if="dismissible"
          type="button"
          class="modal-close"
          aria-label="Close dialog"
          @click="dismiss"
        >
          <AppIcon name="close" />
        </button>
      </header>
      <div class="modal-body"><slot /></div>
      <footer v-if="$slots.footer" class="modal-footer">
        <slot name="footer" />
      </footer>
    </dialog>
  </Teleport>
</template>

<style scoped>
.ui-modal {
  box-sizing: border-box;
  width: min(640px, calc(100vw - 32px));
  max-width: calc(100vw - 32px);
  max-height: calc(100dvh - 32px);
  margin: auto;
  padding: 0;
  border: 1px solid var(--ui-border);
  border-radius: var(--ui-radius-dialog);
  background: var(--ui-surface);
  color: var(--ui-text);
  box-shadow: var(--ui-elevation);
  font: var(--ui-font-body)/1.5 var(--ui-font-family);
  overflow: auto;
}
.ui-modal[open] {
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.ui-modal-wide {
  width: min(1120px, calc(100vw - 64px));
}
.ui-modal-editor {
  width: min(920px, calc(100vw - 32px));
}
.ui-modal-bounded {
  height: min(760px, calc(100dvh - 32px));
}
.ui-modal-bounded .modal-body {
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  padding: 0;
  overflow: hidden;
}
.ui-modal::backdrop {
  background: var(--ui-overlay);
}
.modal-header {
  flex-shrink: 0;
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  padding: 24px;
}
.modal-header-hero {
  align-items: center;
  padding: 16px 20px;
  border-bottom: 1px solid var(--ui-border-soft);
  background: linear-gradient(160deg, var(--ui-accent-soft), transparent 70%);
}
.modal-header h2 {
  margin: 0;
  font: var(--ui-weight-heading) var(--ui-font-heading)/1.4
    var(--ui-font-family);
  overflow-wrap: anywhere;
}
.modal-header p {
  margin: 8px 0 0;
  color: var(--ui-dim);
  font-size: var(--ui-font-small);
}
.modal-close {
  flex-shrink: 0;
  width: var(--ui-control-height);
  height: var(--ui-control-height);
  display: grid;
  place-items: center;
  border: 1px solid var(--ui-border);
  border-radius: var(--ui-radius-control);
  background: var(--ui-surface);
  color: var(--ui-dim);
  cursor: pointer;
}
.modal-body {
  padding: 0 24px 24px;
  overflow-y: auto;
  min-height: 0;
}
.modal-footer {
  flex-shrink: 0;
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 8px;
  padding: 16px 24px;
  border-top: 1px solid var(--ui-border-soft);
}
.ui-modal-bounded .modal-footer {
  padding: 12px 20px;
}
@media (max-width: 760px) {
  .ui-modal {
    width: calc(100vw - 20px);
    max-width: calc(100vw - 20px);
    max-height: calc(100dvh - 20px);
    border-radius: 28px;
  }
  .ui-modal-bounded {
    height: calc(100dvh - 20px);
  }
  .modal-header {
    padding: 20px;
  }
  .modal-header-hero {
    padding: 14px 16px;
  }
  .modal-body {
    padding: 0 20px 20px;
  }
  .modal-footer {
    padding: 16px 20px;
  }
  .ui-modal-bounded .modal-footer {
    padding: 10px 16px;
  }
}
</style>
