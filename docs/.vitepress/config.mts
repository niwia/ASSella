import { defineConfig } from 'vitepress'

export default defineConfig({
  title: 'ASSella',
  description: 'Fast, modern Steam game and DLC management for SteamOS and Linux.',
  base: '/',
  cleanUrls: true,
  ignoreDeadLinks: true,
  srcExclude: ['**/DEPENDENCIES.md', '**/OPTIONAL_COMPONENTS.md', '**/THEMES.md'],
  head: [
    ['link', { rel: 'icon', type: 'image/png', href: '/logo.png' }]
  ],
  themeConfig: {
    logo: '/logo.png',
    siteTitle: 'ASSella',
    nav: [
      { text: 'Home', link: '/' },
      { text: 'Installation Guide', link: '/install' },
      { text: 'Arch', link: '/arch-repo' },
      { text: 'Uran', link: '/uran' },
      {
        text: 'Documentation',
        items: [
          { text: 'Themes', link: '/themes' },
          { text: 'Dependencies', link: '/dependencies' },
          { text: 'Optional Components', link: '/optional-components' },
          { text: 'Game Mode Boot Loop Fix (TTY)', link: '/recovery-tty' }
        ]
      },
      { text: 'Releases', link: 'https://github.com/niwia/ASSella/releases' }
    ],
    sidebar: [
      {
        text: 'Getting Started',
        items: [
          { text: 'Installation Guide', link: '/install' },
          { text: 'Arch', link: '/arch-repo' },
          { text: 'Uran (Decky Plugin)', link: '/uran' }
        ]
      },
      {
        text: 'Reference & Guides',
        items: [
          { text: 'Themes Guide', link: '/themes' },
          { text: 'Dependencies', link: '/dependencies' },
          { text: 'Optional Components', link: '/optional-components' },
          { text: 'Game Mode Boot Loop Fix (TTY)', link: '/recovery-tty' }
        ]
      }
    ],
    socialLinks: [
      { icon: 'github', link: 'https://github.com/niwia/ASSella' }
    ],
    search: {
      provider: 'local'
    },
    footer: {
      message: 'Assella is a personal passion project, built on top of the Lain project. It was developed over several months by a swarm of AI agents, and tested by a small group of human volunteers.',
      copyright: 'AI agents involved: Google Gemini (3.8, 3.5, 2.5) · Anthropic Claude (Sonnet 5.5, 4.6) · DeepSeek 4.1 · Kimi 2.5 · MiniMax M3.1 · GLM 5.3 · Codex GPT 6 Luna'
    }
  }
})
