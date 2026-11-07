# Plugins

The plugins are small Lua scripts that run inside Steam through SLSsteam. They're what make [AT0-M](/at0m) downloads work. You install and update them in **Settings → at0-m → Manage plugins**.

| Plugin | In one line | Needed? |
| :--- | :--- | :--- |
| **SpaceBunny** | Gives Steam the depots, decryption keys and manifest request codes it needs to download your games. | Required |
| **Styx** | Gives games Steam's ownership "tickets", so SteamStub-protected games start. | Required |
| **Lobster** | Puts your depots and keys back into `config.yaml` if an SLSsteam update removes them. | Required |
| **Lettuce** | Retries blocked requests once through your proxy or a Cloudflare worker, for when your internet provider blocks a site. | Optional (on by default) |
| **Pineapple** | Puts Hubcap manifests into Steam's cache ahead of time, so updates still work when every code provider is down. | Coming soon |

## How they work together

```
Steam wants a depot
      │
      ▼
 SpaceBunny ── asks the code providers directly (primary, then rescue)
      │           │
      │           └─ all failed? ──► Lettuce retries each one once through a proxy/worker
      │
      ├─ hands Steam the key + code ──► Steam downloads
      │
 Styx ── makes the game's ownership check pass when it starts
 Lobster ── keeps config.yaml complete in the background
```

## Where they live

- The plugins: `~/.config/SLSsteam/plugins/`
- Their settings: `~/.config/SLSsteam/katamari.conf.lua`, written by ASSella (**Settings → at0-m → Advanced**). The Hubcap key is also shared there with Uran and Epsilon.

::: tip Uran and Epsilon
[Uran](/uran) and [Epsilon](/epsilon) don't ship their own plugins. They use the ones ASSella installed. If an AT0-M game added from Uran or Epsilon won't download, check **Settings → at0-m → Manage plugins** in ASSella first.
:::

## Custom plugins

Only use **I'm using custom plugins (Advanced)** if you run your own plugin set. ASSella then stops checking for or updating its plugins. Plugins that ASSella doesn't know about are listed under **Review**, because they can clash with SpaceBunny and Styx.
