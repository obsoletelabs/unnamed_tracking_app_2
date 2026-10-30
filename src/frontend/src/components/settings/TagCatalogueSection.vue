<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { currentUser } from "../../state/auth";
import {
  addDefaultAlias,
  addTagAlias,
  createDefaultTag,
  createTag,
  deleteDefaultTag,
  deleteTag,
  discoverTags,
  fetchAliasSuggestions,
  fetchTagCatalogue,
  fetchTagUsage,
  foldTagKey,
  removeDefaultAlias,
  removeTagAlias,
  renameDefaultTag,
  renameTag,
  setTagSuppressed,
  type AliasSuggestion,
  type CatalogueTag,
  type TagUsage,
} from "../../services/tagCatalogue";

const tags = ref<CatalogueTag[]>([]);
const defaults = ref<CatalogueTag[]>([]);
const suppressed = ref<string[]>([]);
const usage = ref<TagUsage[]>([]);
const suggestions = ref<AliasSuggestion[]>([]);
const isLoading = ref(true);
const error = ref("");
const notice = ref("");
const newTag = ref("");
const newDefault = ref("");
const aliasDraft = ref<Record<string, string>>({});
const renameDraft = ref<Record<string, string>>({});

const isAdmin = computed(() => !!currentUser.value?.is_admin);
const sourceTags = computed(() =>
  tags.value.filter((t) => t.kind === "source"),
);
const ownTags = computed(() => tags.value.filter((t) => t.kind === "user"));
const suppressedKeys = computed(() => new Set(suppressed.value));
const duplicateHint = computed(() => {
  const key = foldTagKey(newTag.value);
  if (!key) return "";
  const match = tags.value.find((t) => t.key === key);
  return match ? `“${match.label}” already exists and will be reused.` : "";
});

async function load() {
  isLoading.value = true;
  error.value = "";
  try {
    const catalogue = await fetchTagCatalogue();
    tags.value = catalogue.tags;
    defaults.value = catalogue.defaults;
    suppressed.value = catalogue.suppressed;
    [usage.value, suggestions.value] = await Promise.all([
      fetchTagUsage(),
      fetchAliasSuggestions(),
    ]);
  } catch (err) {
    error.value = err instanceof Error ? err.message : "Failed to load tags.";
  } finally {
    isLoading.value = false;
  }
}

// One place for the shared error/notice handling around every change.
async function run(action: () => Promise<unknown>, done = "") {
  error.value = "";
  notice.value = "";
  try {
    await action();
    notice.value = done;
    await load();
  } catch (err) {
    error.value = err instanceof Error ? err.message : "That change failed.";
  }
}

function rawSpellingsOf(label: string): number {
  return usage.value.filter((u) => u.label === label).length;
}

const addTag = () =>
  run(async () => {
    await createTag(newTag.value);
    newTag.value = "";
  });
const saveRename = (tag: CatalogueTag) =>
  run(
    () => renameTag(tag.id, renameDraft.value[tag.id] ?? tag.label),
    "Renamed.",
  );
const removeTag = (tag: CatalogueTag) => {
  if (
    window.confirm(
      `Remove “${tag.label}” from your catalogue? Tags on your games and media are not changed.`,
    )
  )
    void run(() => deleteTag(tag.id), "Removed from the catalogue.");
};
const addAlias = (tag: CatalogueTag) =>
  run(async () => {
    await addTagAlias(tag.id, aliasDraft.value[tag.id] ?? "");
    aliasDraft.value[tag.id] = "";
  });
const acceptSuggestion = (suggestion: AliasSuggestion) =>
  run(async () => {
    const target = await createTag(suggestion.tag);
    await addTagAlias(target.id, suggestion.alias);
  }, `“${suggestion.alias}” now counts as “${suggestion.tag}”.`);
const discover = () =>
  run(async () => {
    const created = await discoverTags();
    notice.value = created
      ? `Added ${created} tags from your library.`
      : "Every tag in your library is already listed.";
  });
const toggleSuppressed = (alias: string, hide: boolean) =>
  run(() => setTagSuppressed(alias, hide));

const addDefault = () =>
  run(async () => {
    await createDefaultTag(newDefault.value);
    newDefault.value = "";
  });
const saveDefaultRename = (tag: CatalogueTag) =>
  run(
    () => renameDefaultTag(tag.id, renameDraft.value[tag.id] ?? tag.label),
    "Renamed.",
  );
const removeDefault = (tag: CatalogueTag) => {
  if (window.confirm(`Remove the default “${tag.label}” for everyone?`))
    void run(() => deleteDefaultTag(tag.id));
};
const addDefaultAliasFor = (tag: CatalogueTag) =>
  run(async () => {
    await addDefaultAlias(tag.id, aliasDraft.value[tag.id] ?? "");
    aliasDraft.value[tag.id] = "";
  });

onMounted(load);
</script>

<template>
  <section class="section" aria-labelledby="tag-catalogue-title">
    <div class="section-header">
      <h2 id="tag-catalogue-title">Tag catalogue</h2>
      <p>
        Rename tags and group spellings that mean the same thing. Your games and
        media keep the tags they came with; this only changes how they are shown
        and counted.
      </p>
    </div>

    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <p v-else-if="notice" class="status" role="status">{{ notice }}</p>
    <p v-if="isLoading" class="muted">Loading…</p>

    <template v-else>
      <form class="add-row" @submit.prevent="addTag">
        <label for="new-tag">New tag</label>
        <input
          id="new-tag"
          v-model="newTag"
          type="text"
          maxlength="40"
          autocomplete="off"
          placeholder="e.g. Roguelike"
        />
        <button type="submit" :disabled="!newTag.trim()">Add</button>
        <p v-if="duplicateHint" class="muted">{{ duplicateHint }}</p>
      </form>

      <div class="block" data-testid="your-tags">
        <h3>Your tags</h3>
        <p v-if="!ownTags.length && !sourceTags.length" class="muted">
          Nothing here yet. Add a tag above, or list the tags already in your
          library below.
        </p>
        <ul class="tag-list">
          <li
            v-for="tag in [...ownTags, ...sourceTags]"
            :key="tag.id"
            class="tag-card"
          >
            <div class="tag-head">
              <input
                :aria-label="`Name for ${tag.label}`"
                :value="renameDraft[tag.id] ?? tag.label"
                maxlength="40"
                @input="
                  renameDraft[tag.id] = (
                    $event.target as HTMLInputElement
                  ).value
                "
                @keydown.enter.prevent="saveRename(tag)"
              />
              <button
                type="button"
                class="secondary"
                :disabled="(renameDraft[tag.id] ?? tag.label) === tag.label"
                @click="saveRename(tag)"
              >
                Rename
              </button>
              <button type="button" class="danger" @click="removeTag(tag)">
                Remove
              </button>
            </div>
            <p class="muted">
              Includes {{ tag.aliases.length + 1 }} spelling{{
                tag.aliases.length ? "s" : ""
              }}<template v-if="tag.kind === 'source'">
                · found in your library</template
              >
            </p>
            <ul class="chips" :aria-label="`Spellings counted as ${tag.label}`">
              <li v-for="alias in tag.aliases" :key="alias.id" class="chip">
                {{ alias.label }}
                <button
                  type="button"
                  :aria-label="`Stop counting ${alias.label} as ${tag.label}`"
                  @click="run(() => removeTagAlias(tag.id, alias.id))"
                >
                  ×
                </button>
              </li>
            </ul>
            <form class="alias-row" @submit.prevent="addAlias(tag)">
              <input
                v-model="aliasDraft[tag.id]"
                type="text"
                maxlength="40"
                :aria-label="`Add a spelling that means ${tag.label}`"
                placeholder="Also count this spelling…"
              />
              <button
                type="submit"
                :disabled="!(aliasDraft[tag.id] ?? '').trim()"
              >
                Add spelling
              </button>
            </form>
          </li>
        </ul>
      </div>

      <div v-if="suggestions.length" class="block" data-testid="suggestions">
        <h3>Possible matches</h3>
        <p class="muted">
          These spellings look alike. Nothing is merged until you choose.
        </p>
        <ul class="suggestion-list">
          <li v-for="s in suggestions" :key="s.tag + s.alias">
            <span>
              <strong>{{ s.alias }}</strong> ({{ s.uses[1] }}) as
              <strong>{{ s.tag }}</strong> ({{ s.uses[0] }})
            </span>
            <button
              type="button"
              class="secondary"
              @click="acceptSuggestion(s)"
            >
              Count as {{ s.tag }}
            </button>
          </li>
        </ul>
      </div>

      <div class="block" data-testid="in-use">
        <h3>Tags in your library</h3>
        <div class="block-actions">
          <button type="button" class="secondary" @click="discover">
            List them in my catalogue
          </button>
        </div>
        <p v-if="!usage.length" class="muted">No tags are in use yet.</p>
        <ul v-else class="usage">
          <li v-for="u in usage.slice(0, 40)" :key="u.tag">
            <span>{{ u.tag }}</span>
            <span v-if="u.label !== u.tag" class="muted"
              >shown as {{ u.label }}</span
            >
            <span class="count">{{ u.uses }}</span>
          </li>
        </ul>
      </div>

      <div
        v-if="defaults.length || isAdmin"
        class="block"
        data-testid="defaults"
      >
        <h3>Defaults for this server</h3>
        <p class="muted">
          Set by an administrator. Your own choices above always win, and you
          can show any spelling as it is.
        </p>
        <ul class="tag-list">
          <li v-for="tag in defaults" :key="tag.id" class="tag-card">
            <template v-if="isAdmin">
              <div class="tag-head">
                <input
                  :aria-label="`Default name for ${tag.label}`"
                  :value="renameDraft[tag.id] ?? tag.label"
                  maxlength="40"
                  @input="
                    renameDraft[tag.id] = (
                      $event.target as HTMLInputElement
                    ).value
                  "
                />
                <button
                  type="button"
                  class="secondary"
                  :disabled="(renameDraft[tag.id] ?? tag.label) === tag.label"
                  @click="saveDefaultRename(tag)"
                >
                  Rename
                </button>
                <button
                  type="button"
                  class="danger"
                  @click="removeDefault(tag)"
                >
                  Remove
                </button>
              </div>
            </template>
            <strong v-else>{{ tag.label }}</strong>
            <ul class="chips">
              <li v-for="alias in tag.aliases" :key="alias.id" class="chip">
                {{ alias.label }}
                <button
                  v-if="isAdmin"
                  type="button"
                  :aria-label="`Remove default spelling ${alias.label}`"
                  @click="run(() => removeDefaultAlias(tag.id, alias.id))"
                >
                  ×
                </button>
                <button
                  v-else
                  type="button"
                  class="text"
                  @click="
                    toggleSuppressed(
                      alias.label,
                      !suppressedKeys.has(foldTagKey(alias.label)),
                    )
                  "
                >
                  {{
                    suppressedKeys.has(foldTagKey(alias.label))
                      ? "Use default"
                      : "Show original"
                  }}
                </button>
              </li>
            </ul>
            <form
              v-if="isAdmin"
              class="alias-row"
              @submit.prevent="addDefaultAliasFor(tag)"
            >
              <input
                v-model="aliasDraft[tag.id]"
                type="text"
                maxlength="40"
                :aria-label="`Add a default spelling for ${tag.label}`"
                placeholder="Also count this spelling…"
              />
              <button
                type="submit"
                :disabled="!(aliasDraft[tag.id] ?? '').trim()"
              >
                Add spelling
              </button>
            </form>
            <p v-if="!isAdmin && rawSpellingsOf(tag.label)" class="muted">
              {{ rawSpellingsOf(tag.label) }} spelling{{
                rawSpellingsOf(tag.label) === 1 ? "" : "s"
              }}
              in your library
            </p>
          </li>
        </ul>
        <form v-if="isAdmin" class="add-row" @submit.prevent="addDefault">
          <label for="new-default">New default tag</label>
          <input
            id="new-default"
            v-model="newDefault"
            type="text"
            maxlength="40"
          />
          <button type="submit" :disabled="!newDefault.trim()">
            Add default
          </button>
        </form>
      </div>
    </template>
  </section>
</template>

<style scoped>
.section {
  color: var(--ui-text);
}
.section-header {
  margin-bottom: 20px;
}
h2 {
  margin: 0 0 8px;
  font: var(--ui-weight-heading) var(--ui-font-heading) / 1.4
    var(--ui-font-family);
}
h3 {
  margin: 0 0 8px;
  font-size: 1rem;
}
.block {
  margin-top: 28px;
}
.muted {
  opacity: 0.75;
  font-size: 0.9rem;
  margin: 4px 0;
}
.error {
  color: var(--ui-error);
}
.status {
  opacity: 0.85;
}
.add-row,
.alias-row,
.tag-head {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}
.add-row label {
  flex-basis: 100%;
  font-weight: 600;
}
input[type="text"],
.tag-head input {
  flex: 1 1 200px;
  min-width: 0;
  padding: 8px 10px;
  border: 1px solid var(--ui-border);
  border-radius: 8px;
  background: var(--ui-bg);
  color: var(--ui-text);
}
button {
  padding: 8px 12px;
  border-radius: 8px;
  border: 1px solid var(--ui-border);
  background: var(--ui-accent);
  color: var(--ui-on-accent);
  cursor: pointer;
}
button:disabled {
  opacity: 0.5;
  cursor: default;
}
button.secondary {
  background: transparent;
  color: var(--ui-text);
}
button.danger {
  background: transparent;
  color: var(--ui-error);
}
button.text {
  padding: 0 4px;
  border: 0;
  background: none;
  color: var(--ui-accent);
  font-size: 0.8rem;
}
.tag-list,
.suggestion-list,
.usage,
.chips {
  list-style: none;
  margin: 0;
  padding: 0;
}
.tag-list {
  display: grid;
  gap: 12px;
}
.tag-card {
  padding: 12px;
  border: 1px solid var(--ui-border);
  border-radius: 12px;
  background: var(--ui-bg);
}
.chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin: 8px 0;
}
.chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 8px;
  border: 1px solid var(--ui-border);
  border-radius: 999px;
  font-size: 0.85rem;
}
.chip button {
  padding: 0 4px;
  border: 0;
  background: none;
  color: var(--ui-text);
}
.suggestion-list li,
.usage li {
  display: flex;
  gap: 12px;
  align-items: center;
  justify-content: space-between;
  padding: 6px 0;
  border-bottom: 1px solid var(--ui-border);
}
.count {
  margin-left: auto;
  font-variant-numeric: tabular-nums;
}
.block-actions {
  margin-bottom: 8px;
}
</style>
