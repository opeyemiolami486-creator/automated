// ==UserScript==
// @name         Udin Brainrot Fortress — Hackathon Controls
// @namespace    https://udin-brainrot-fortress.lovable.app/
// @version      1.0.0
// @description  Local-only hackathon helper: add coins and lives through the game's client-side engine.
// @author       Hackathon participant
// @match        https://udin-brainrot-fortress.lovable.app/*
// @run-at       document-idle
// @grant        none
// ==/UserScript==

(() => {
  'use strict';

  const PANEL_ID = 'udin-hackathon-controls';
  const COIN_INCREMENT = 5000;
  const LIFE_INCREMENT = 1;
  const MAX_LIFE_BONUS = 99;

  // The game owns this instance; the userscript only changes local in-memory state.
  let cachedGame = null;

  function getGameInstance() {
    const canvas = document.querySelector('canvas');
    if (!canvas) return null;

    const fiberKey = Object.keys(canvas).find((key) => key.startsWith('__reactFiber'));
    if (!fiberKey) return null;

    let fiber = canvas[fiberKey];
    const visited = new Set();

    // The game component stores its engine in a React ref hook. Walk up the fiber
    // tree and inspect hook memoizedState values without relying on minified names.
    for (let level = 0; fiber && level < 80; level += 1, fiber = fiber.return) {
      if (visited.has(fiber)) break;
      visited.add(fiber);

      let hook = fiber.memoizedState;
      for (let index = 0; hook && index < 40; index += 1, hook = hook.next) {
        const value = hook.memoizedState;
        const candidates = [
          value,
          value && value.current,
          value && value.inst && value.inst.value,
          value && value.inst && value.inst.memoizedState,
        ];

        for (const candidate of candidates) {
          if (
            candidate &&
            typeof candidate === 'object' &&
            typeof candidate.emit === 'function' &&
            typeof candidate.startNextWave === 'function' &&
            typeof candidate.useAbility === 'function' &&
            typeof candidate.coins === 'number' &&
            typeof candidate.lives === 'number'
          ) {
            cachedGame = candidate;
            return candidate;
          }
        }
      }
    }

    return cachedGame && typeof cachedGame.emit === 'function' ? cachedGame : null;
  }

  function updateStatus(message, kind = 'ok') {
    const status = document.querySelector(`#${PANEL_ID} [data-udin-status]`);
    if (!status) return;
    status.textContent = message;
    status.dataset.kind = kind;
    window.clearTimeout(updateStatus.timer);
    updateStatus.timer = window.setTimeout(() => {
      status.textContent = 'Ready';
      status.dataset.kind = 'ok';
    }, 2400);
  }

  function withGame(action) {
    const game = getGameInstance();
    if (!game) {
      updateStatus('Game engine not ready', 'error');
      return false;
    }

    try {
      action(game);
      game.emit();
      return true;
    } catch (error) {
      console.error('[Udin hackathon controls]', error);
      updateStatus('Could not update game', 'error');
      return false;
    }
  }

  function addCoins() {
    if (withGame((game) => {
      game.coins = Math.max(0, Math.floor(game.coins) + COIN_INCREMENT);
    })) {
      updateStatus(`+${COIN_INCREMENT.toLocaleString()} coins`);
    }
  }

  function addLife() {
    if (withGame((game) => {
      game.lives = Math.min(MAX_LIFE_BONUS, Math.max(0, Math.floor(game.lives) + LIFE_INCREMENT));
      // A life refill is useful after a game-over transition in the local client.
      if (game.lives > 0) game.gameOver = false;
    })) {
      updateStatus('+1 life');
    }
  }

  function createPanel() {
    if (document.getElementById(PANEL_ID)) return;

    const panel = document.createElement('section');
    panel.id = PANEL_ID;
    panel.innerHTML = `
      <div class="udin-header">
        <div>
          <strong>Hackathon tools</strong>
          <small>Local game controls</small>
        </div>
        <button type="button" class="udin-collapse" data-udin-collapse aria-expanded="true" title="Collapse controls">−</button>
      </div>
      <div class="udin-body" data-udin-body>
        <button type="button" class="udin-action" data-udin-coins>
          <span><b>🪙</b> Add 5,000 coins</span>
          <span class="udin-toggle" aria-hidden="true"><i></i></span>
        </button>
        <button type="button" class="udin-action" data-udin-life>
          <span><b>❤</b> Add 1 life</span>
          <span class="udin-toggle" aria-hidden="true"><i></i></span>
        </button>
        <div class="udin-status" data-udin-status data-kind="ok">Ready</div>
        <small class="udin-note">Changes stay in this browser tab.</small>
      </div>
    `;

    const style = document.createElement('style');
    style.textContent = `
      #${PANEL_ID} {
        position: fixed;
        z-index: 2147483647;
        top: 12px;
        left: 12px;
        width: min(290px, calc(100vw - 24px));
        color: #fff7ed;
        background: linear-gradient(145deg, rgba(65, 24, 2, .97), rgba(27, 10, 1, .97));
        border: 1px solid rgba(255, 138, 31, .78);
        border-radius: 12px;
        box-shadow: 0 8px 30px rgba(0,0,0,.45), 0 0 18px rgba(255,138,31,.2);
        font: 13px/1.35 ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        overflow: hidden;
        user-select: none;
      }
      #${PANEL_ID} * { box-sizing: border-box; }
      #${PANEL_ID} .udin-header { display:flex; align-items:center; justify-content:space-between; gap:10px; padding:10px 10px 9px 12px; }
      #${PANEL_ID} strong { display:block; color:#ffb15c; font-size:13px; }
      #${PANEL_ID} .udin-header small { display:block; margin-top:1px; color:rgba(255,237,213,.65); font-size:10px; }
      #${PANEL_ID} .udin-collapse { width:25px; height:25px; border:1px solid rgba(255,177,92,.55); border-radius:7px; color:#ffd7a3; background:rgba(255,138,31,.14); cursor:pointer; font-size:18px; line-height:18px; }
      #${PANEL_ID} .udin-collapse:hover { background:rgba(255,138,31,.3); }
      #${PANEL_ID} .udin-body { padding:0 10px 10px; }
      #${PANEL_ID} .udin-action { display:flex; width:100%; align-items:center; justify-content:space-between; gap:10px; margin-top:7px; padding:9px 10px; border:1px solid rgba(255,255,255,.13); border-radius:9px; color:#fff7ed; background:rgba(255,255,255,.07); cursor:pointer; text-align:left; font:inherit; }
      #${PANEL_ID} .udin-action:hover { border-color:#ff9f43; background:rgba(255,138,31,.2); }
      #${PANEL_ID} .udin-action:active { transform:translateY(1px); }
      #${PANEL_ID} .udin-action b { display:inline-block; width:20px; font-size:15px; }
      #${PANEL_ID} .udin-toggle { width:31px; height:17px; flex:0 0 auto; padding:2px; border-radius:999px; background:#6b3211; transition:background .15s; }
      #${PANEL_ID} .udin-action:hover .udin-toggle { background:#f97316; }
      #${PANEL_ID} .udin-toggle i { display:block; width:13px; height:13px; border-radius:50%; background:#fed7aa; transition:transform .15s; }
      #${PANEL_ID} .udin-action:hover .udin-toggle i { transform:translateX(14px); background:#fff7ed; }
      #${PANEL_ID} .udin-status { min-height:18px; margin:8px 2px 0; color:#86efac; font-size:11px; }
      #${PANEL_ID} .udin-status[data-kind="error"] { color:#fca5a5; }
      #${PANEL_ID} .udin-note { display:block; color:rgba(255,237,213,.52); font-size:10px; }
      #${PANEL_ID}[data-collapsed="true"] .udin-body { display:none; }
      #${PANEL_ID}[data-collapsed="true"] .udin-collapse { font-size:16px; }
    `;
    document.head.appendChild(style);
    document.body.appendChild(panel);

    panel.querySelector('[data-udin-collapse]').addEventListener('click', () => {
      const collapsed = panel.dataset.collapsed === 'true';
      panel.dataset.collapsed = String(!collapsed);
      const button = panel.querySelector('[data-udin-collapse]');
      button.textContent = collapsed ? '−' : '+';
      button.setAttribute('aria-expanded', String(collapsed));
      button.title = collapsed ? 'Collapse controls' : 'Expand controls';
    });
    panel.querySelector('[data-udin-coins]').addEventListener('click', addCoins);
    panel.querySelector('[data-udin-life]').addEventListener('click', addLife);
  }

  function boot() {
    createPanel();
    getGameInstance();
  }

  boot();
  const observer = new MutationObserver(() => {
    if (!document.getElementById(PANEL_ID)) createPanel();
    if (!cachedGame) getGameInstance();
  });
  observer.observe(document.documentElement, { childList: true, subtree: true });
  window.setInterval(() => { if (!cachedGame) getGameInstance(); }, 1000);

  window.__UDIN_HACKATHON_CONTROLS__ = Object.freeze({ addCoins, addLife, getGameInstance });
})();
