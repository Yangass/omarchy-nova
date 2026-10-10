# NOVA

**Run AI models on your own computer, without the terminal.**

NOVA is a plugin for [Omarchy](https://omarchy.org) that takes you from a fresh install to
chatting with a local AI model in a few clicks. It looks at your hardware, shows you
which models will actually run well on it, downloads the one you pick, and gives you a
chat window in your top bar. Your conversations never leave your computer.

It's made for people who are **new to Omarchy or Linux** and don't want to learn
`ollama pull`, VRAM math and systemd before trying a local model. If you know all that
already, NOVA still saves you the guesswork.

![Models that fit this computer](docs/screenshots/models.png)

## What it does for you

- **Picks models that fit your PC.** NOVA reads your graphics card, memory and disk,
  and marks every model in its list as a comfortable fit, a tight fit, or too big,
  *before* you download anything. Suggestions come from published guides and say so.
- **Installs them for you.** Click **INSTALL**, read the approval card (size, disk
  space left afterwards, fit), click **Approve**. NOVA downloads the model and checks
  that the file is exactly the version it checked. No commands to type.
- **Gives you a chat in the top bar.** Answers stream in; NOVA knows it's on Omarchy
  and can look up live information about your machine when you ask.
- **Shows what your machine is doing.** CPU, graphics, memory, disk, network, and which
  model is currently loaded, with one click to free its memory.
- **Never does anything behind your back.** Every download, deletion, restart or app
  launch waits for your **Approve** on a card that says exactly what will happen.

<p>
  <img src="docs/screenshots/chat.png" width="32%" alt="CHAT: NOVA describing itself">
  <img src="docs/screenshots/machine.png" width="32%" alt="MACHINE: live hardware use">
  <img src="docs/screenshots/approval.png" width="32%" alt="An approval card">
</p>

## Install (about 5 minutes, plus the model download)

You need Omarchy and an internet connection. A graphics card helps but isn't
required: NOVA suggests smaller models for computers without one.

1. Press **Super + Space** to open the Omarchy menu, then go to **Setup → Plugins →
   Add Plugin**, and paste this address:
   ```
   https://github.com/Yangass/omarchy-nova
   ```
   Omarchy then warns that plugins run code on your computer. It shows this for every
   plugin; [docs/HOW_IT_WORKS.md](docs/HOW_IT_WORKS.md) explains what NOVA runs.
   Answer **Yes** to enable it and keep the suggested position in the bar.
2. Click the new NOVA icon in the top bar. NOVA shows one step at a time:
   1. **SET UP NOVA** installs its small background helpers (no password needed).
   2. **INSTALL OLLAMA** installs the free engine that runs the models, if you don't
      have it yet. A terminal opens and asks for your password once; that's all the
      terminal you'll need.
   3. Open **MODELS** and click **BROWSE MODEL DOWNLOADS** at the bottom. Pick a model
      marked as a good fit, click its **INSTALL** button and approve.
3. When the download finishes, click **LOAD AND USE** and start chatting in **CHAT**.

Models take 1–25 GB of disk space each; NOVA tells you the size before you start.

## Update

NOVA checks once a week for a newer version and shows **UPDATE NOVA** in the panel.
Click it and press Enter in the window that opens.

## Remove

Open a terminal (**Super + Enter**) and paste:

```bash
bash ~/.config/omarchy/plugins/io.github.yangass.nova/tools/nova-uninstall.sh
```

This removes NOVA's background helpers and the plugin. Ollama and the models you
downloaded stay, so other tools can keep using them (`ollama list` shows them,
`ollama rm <name>` deletes one).

## Privacy

Your chats and files stay on your computer. NOVA uses the internet only to:

- read model details from Ollama's library and download the models you approve;
- check once a week whether a newer NOVA exists.

Its helpers listen only on your own computer (`127.0.0.1`). No account, no telemetry.

## Good to know

- **Requirements:** Omarchy (Arch Linux + Hyprland). Python and everything else NOVA
  needs come with Omarchy; Ollama 0.6.5 or newer is installed for you if missing.
- **Graphics cards:** NVIDIA, AMD and Intel are recognised. Without a dedicated card
  models run on the processor: slower, but small models work fine.
- **More models:** anything from [Ollama's library](https://ollama.com/library) can be
  added under **ADD A MODEL**; NOVA checks it the same way before you install.
- **Coding CLI (advanced):** NOVA can open a coding assistant in a terminal that uses
  a local model. It needs a setup file, so the **OPEN CLI** button only appears once
  one exists; a guided setup is planned.
- **How it works inside:** [docs/HOW_IT_WORKS.md](docs/HOW_IT_WORKS.md).

## Who makes it

NOVA is built by one person, [Konstantin Tarasevic](https://github.com/Yangass), with AI
doing much of the engineering: Claude as lead developer and reviewer, and a local Qwen
model (running through NOVA) for small, well-defined tasks. Every change is covered by
automated tests, and releases are tried on a fresh Omarchy machine before they ship.

Found a problem or have an idea? [Open an issue](https://github.com/Yangass/omarchy-nova/issues).

## License

[MIT](LICENSE)
