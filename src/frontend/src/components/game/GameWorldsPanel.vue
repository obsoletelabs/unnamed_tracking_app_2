<script setup lang="ts">
import type { TrashedGameFile } from "../../services/media";
import GameMediaPanel from "../GameMediaPanel.vue";
import GameArchivesPanel from "../GameArchivesPanel.vue";
import ArchiveCard from "../ArchiveCard.vue";
import { useGameDetailContext } from "../../composables/gameDetailContext";
const {
  game,
  modpackFiles,
  filesLoaded,
  filesError,
  uploadingFiles,
  onGameFilesSelected,
  saveGameFile,
  bulkSaveFiles,
  removeGameFile,
  modpackTrash,
  restoreFileItem,
  saveUploading,
  openArchiveEdit,
  bulkDeleteArchives,
  onDeleteArchive,
  worldTrash,
  onRestoreArchive,
  worlds,
  worldsLoaded,
  onNewWorldSelected,
  onAddWorldVersion,
} = useGameDetailContext();
</script>
<template>
  <section v-if="game" class="worlds-panel">
    <GameArchivesPanel
      scoped
      title="Worlds"
      plural="worlds"
      singular="world"
      hint="Zip the world folder (the one containing level.dat), then drop it here. You'll be asked to name it."
      :archives="worlds"
      :trash="worldTrash"
      :loaded="worldsLoaded"
      :uploading="saveUploading.has('')"
      :error="filesError"
      @files="onNewWorldSelected"
      @bulk-delete="bulkDeleteArchives($event, true)"
      @restore="onRestoreArchive($event, true)"
      @problem="filesError = $event"
    >
      <template #card="{ archive: world, selecting, selected, toggle }">
        <ArchiveCard
          :archive="world"
          kind="world"
          :selecting="selecting"
          :selected="selected"
          :uploading="saveUploading.has(world.id)"
          @toggle="toggle"
          @edit="openArchiveEdit($event, true)"
          @delete="onDeleteArchive($event, true)"
          @add-version="onAddWorldVersion"
        />
      </template>
    </GameArchivesPanel>

    <div class="modpack-block">
      <GameMediaPanel
        scoped
        kind="modpack"
        :items="modpackFiles"
        :trash="modpackTrash"
        :loading="filesLoaded.modpack === null"
        :uploading="uploadingFiles"
        :error="null"
        @files="onGameFilesSelected($event, 'modpack')"
        @delete="removeGameFile('modpack', $event)"
        @save="(item, patch) => saveGameFile('modpack', item, patch)"
        @bulk-save="bulkSaveFiles('modpack', $event)"
        @bulk-delete="
          (items) => items.forEach((f) => removeGameFile('modpack', f))
        "
        @restore="restoreFileItem('modpack', $event as TrashedGameFile)"
        @problem="filesError = $event"
      />
    </div>
  </section>
</template>
