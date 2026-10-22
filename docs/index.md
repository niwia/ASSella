---
layout: home

hero:
  name: "ASSella"
  text: "Make linux great again"
  tagline: "Depotdownloader made with AI on steroids."
  image:
    src: /logo.png
    alt: ASSella Logo
  actions:
    - theme: brand
      text: Installation Guide
      link: /install
    - theme: brand
      text: Uran (Decky Plugin)
      link: /uran
    - theme: alt
      text: View on GitHub
      link: https://github.com/niwia/ASSella
---

## Quick Install

To install ASSella directly on Steam Deck or Linux terminal:

```bash
curl -fsSL https://raw.githubusercontent.com/niwia/ASSella/beta/install.sh | bash
```

Read the full step-by-step setup in the [Installation Guide](/install).

<script setup>
import { ref, onMounted, onUnmounted } from 'vue'

const ALL_CONTRIBUTORS = [
  'YesYes', 'Goldberg', 'Detanup01', 'atom0s', 'acidicoala', 'Depotdownloadermod',
  'drazy', 'ACEsls', 'p.t.r', 'gogovang', 'morrenus', 'da_real_dari',
  'bleibeidl', 'beastzy', 'Shinji257', 'th3g3ntlem3n', 'tr4nquility',
  'kaiser_1804', 'feelingkindadizzy', 'aguga', 'shisaya', 'jayo',
  'ansh2209', 'pduran5', 'MohandL3G'
]

const SLOT_COUNT = 9

// Initial distribution
const slots = ref(
  Array.from({ length: SLOT_COUNT }, (_, i) => ({
    name: ALL_CONTRIBUTORS[i % ALL_CONTRIBUTORS.length],
    visible: true,
    history: [ALL_CONTRIBUTORS[i % ALL_CONTRIBUTORS.length]]
  }))
)

let activeTimers = []

function pickRandom(list) {
  return list[Math.floor(Math.random() * list.length)]
}

function runSlotLoop(idx, delay) {
  const step = () => {
    // 1. Fade out current name
    if (slots.value[idx]) {
      slots.value[idx].visible = false
    }

    // 2. While hidden, select a new randomized name that isn't currently displayed elsewhere
    // and hasn't been in this specific slot for the last 3 rotations
    const swapTimer = setTimeout(() => {
      if (!slots.value[idx]) return

      const currentlyShown = slots.value.map(s => s.name)
      const slotHistory = slots.value[idx].history || []

      // Candidates not shown anywhere right now AND not in this slot's recent history
      let available = ALL_CONTRIBUTORS.filter(
        name => !currentlyShown.includes(name) && !slotHistory.includes(name)
      )

      // Fallback if pool is constrained
      if (available.length === 0) {
        available = ALL_CONTRIBUTORS.filter(name => !currentlyShown.includes(name))
      }
      if (available.length === 0) {
        available = ALL_CONTRIBUTORS.filter(name => name !== slots.value[idx].name)
      }

      const nextName = pickRandom(available)
      const updatedHistory = [...slotHistory, nextName].slice(-3)

      slots.value[idx].name = nextName
      slots.value[idx].history = updatedHistory

      // 3. Fade in new name
      slots.value[idx].visible = true

      // 4. Stay visible for 4.5s - 7.5s before next swap
      const dwellTime = 4500 + Math.random() * 3000
      const nextCycleTimer = setTimeout(step, dwellTime)
      activeTimers.push(nextCycleTimer)
    }, 1800) // matches fade-out transition duration

    activeTimers.push(swapTimer)
  }

  const initialTimer = setTimeout(step, delay)
  activeTimers.push(initialTimer)
}

onMounted(() => {
  // Stagger each slot's swap so they never all change at once
  slots.value.forEach((_, i) => {
    const initialStagger = 2500 + i * 1600 + Math.random() * 1200
    runSlotLoop(i, initialStagger)
  })
})

onUnmounted(() => {
  activeTimers.forEach(id => clearTimeout(id))
  activeTimers = []
})
</script>

<div class="testers-section">
  <p class="testers-title">Special thanks to early testers &amp; contributors</p>
  <div class="testers-cloud">
    <div
      v-for="(slot, i) in slots"
      :key="i"
      class="tester-slot"
      :class="{ 'is-visible': slot.visible }"
    >
      <span class="tester-name">{{ slot.name }}</span>
    </div>
  </div>
</div>

<style scoped>
.testers-section {
  margin-top: 3.5rem;
  margin-bottom: 0.5rem;
  padding: 0 1rem;
  text-align: center;
  width: 100%;
}
.testers-title {
  font-size: 0.78rem;
  font-weight: 600;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--vp-c-text-3);
  margin-bottom: 2rem;
}
.testers-cloud {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  align-items: center;
  gap: 2rem 3.5rem;
  max-width: 960px;
  margin: 0 auto;
  padding: 0.5rem 0 1.5rem;
  min-height: 160px;
}
.tester-slot {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 130px;
  padding: 0.25rem 0.5rem;
  opacity: 0;
  transform: translateX(-18px) scale(0.92);
  filter: blur(1.5px);
  will-change: transform, opacity;
  transition: opacity 1.8s ease-in-out, transform 2.4s cubic-bezier(0.25, 1, 0.5, 1), filter 1.8s ease-in-out;
}
.tester-slot.is-visible {
  opacity: 0.95;
  transform: translateX(12px) scale(1);
  filter: blur(0px);
}
.tester-name {
  font-family: var(--vp-font-family-mono);
  font-size: 0.92rem;
  font-weight: 500;
  color: var(--vp-c-brand-1);
  white-space: nowrap;
  user-select: none;
}
</style>

