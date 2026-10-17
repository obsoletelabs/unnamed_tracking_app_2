<script setup lang="ts">
// Draws the server's safe node tree. Everything is text interpolation or a
// fixed element, never v-html, so content cannot introduce markup.
import { RouterLink } from "vue-router";
import { headingTag, safeHref } from "../utils/richNodes";
import type { RichNode } from "../utils/richNodes";

defineProps<{ nodes: RichNode[] }>();
</script>

<template>
  <template v-for="(node, index) in nodes" :key="index">
    <template v-if="node.t === 'text'">{{ node.v }}</template>
    <br v-else-if="node.t === 'br'" />
    <code v-else-if="node.t === 'code'">{{ node.v }}</code>
    <strong v-else-if="node.t === 'strong'"
      ><RichNodes :nodes="node.c ?? []"
    /></strong>
    <em v-else-if="node.t === 'em'"><RichNodes :nodes="node.c ?? []" /></em>
    <template v-else-if="node.t === 'link'">
      <a
        v-if="safeHref(node.href)?.kind === 'external'"
        :href="safeHref(node.href)!.href"
        target="_blank"
        rel="noopener noreferrer nofollow"
        ><RichNodes :nodes="node.c ?? []"
      /></a>
      <RouterLink
        v-else-if="safeHref(node.href)?.kind === 'route'"
        :to="safeHref(node.href)!.href"
        ><RichNodes :nodes="node.c ?? []"
      /></RouterLink>
      <RichNodes v-else :nodes="node.c ?? []" />
    </template>
    <p v-else-if="node.t === 'p'"><RichNodes :nodes="node.c ?? []" /></p>
    <component :is="headingTag(node.level)" v-else-if="node.t === 'h'"
      ><RichNodes :nodes="node.c ?? []"
    /></component>
    <ul v-else-if="node.t === 'ul'">
      <li v-for="(item, at) in node.c ?? []" :key="at">
        <RichNodes :nodes="item.c ?? []" />
      </li>
    </ul>
    <ol v-else-if="node.t === 'ol'">
      <li v-for="(item, at) in node.c ?? []" :key="at">
        <RichNodes :nodes="item.c ?? []" />
      </li>
    </ol>
    <blockquote v-else-if="node.t === 'quote'">
      <RichNodes :nodes="node.c ?? []" />
    </blockquote>
    <pre
      v-else-if="node.t === 'pre'"
      tabindex="0"
    ><code>{{ node.v }}</code></pre>
    <hr v-else-if="node.t === 'hr'" />
  </template>
</template>
