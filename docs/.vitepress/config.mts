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
      { text: 'Installation Guide', link: '/install' },
      { text: 'arm64', link: '/arm64' },
      { text: 'Uran', link: '/uran' },
      { text: 'Epsilon', link: '/epsilon' },
      {
        text: 'Documentation',
        items: [
          { text: 'Command Line Interface (CLI)', link: '/cli' },
          { text: 'Online Multiplayer', link: '/online-multiplayer' },
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
        text: 'ASSella',
        items: [
          { text: 'Installation Guide', link: '/install' },
          {
            text: 'Downloading Games',
            link: '/downloading',
            items: [
              { text: 'Bring Your Own .lua', link: '/guides/import-lua' }
            ]
          },
          { text: 'Library', link: '/library' },
          { text: 'Game Details', link: '/game-details' },
          {
            text: 'AT0-M',
            link: '/at0m',
            items: [
              { text: 'Plugins', link: '/plugins' }
            ]
          },
          {
            text: 'Guides',
            collapsed: false,
            items: [
              { text: 'Downgrade a Game', link: '/guides/downgrade' },
              { text: 'Pin a Build', link: '/guides/pin-build' },
              { text: 'Uninstall a Game', link: '/guides/uninstall' },
              { text: 'Launcher Bypass', link: '/guides/launcher-bypass' }
            ]
          },
          { text: 'Why ASSella?', link: '/compare' }
        ]
      },
      {
        text: 'arm64',
        items: [{ text: 'arm64 (coming soon)', link: '/arm64' }]
      },
      {
        text: 'Uran',
        items: [{ text: 'Uran (Decky Plugin)', link: '/uran' }]
      },
      {
        text: 'Epsilon',
        items: [{ text: 'Epsilon (Browser Bridge)', link: '/epsilon' }]
      },
      {
        text: 'Documentation',
        collapsed: true,
        items: [
          { text: 'Command Line Interface (CLI)', link: '/cli' },
          { text: 'Online Multiplayer Guide', link: '/online-multiplayer' },
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
