document.addEventListener("DOMContentLoaded", () => {
  const authMode = document.body.dataset.authMode || "login";
  const tabs = Array.from(document.querySelectorAll(".auth-tab"));
  const panels = Array.from(document.querySelectorAll(".auth-panel"));
  const bpSyncRoot = document.querySelector("[data-bp-sync-url][data-bp-sync-key]");
  const ladderExpiredAlert = document.querySelector("[data-ladder-expired-alert]");
  const ladderChatDock = document.querySelector("[data-ladder-chat-dock]");
  const ladderChatTabs = Array.from(document.querySelectorAll("[data-ladder-chat-tab]"));
  const ladderChatPanels = Array.from(document.querySelectorAll("[data-ladder-chat-panel]"));
  const ladderChatToggle = document.querySelector("[data-ladder-chat-toggle]");
  const ladderChatToggleLabel = document.querySelector("[data-ladder-chat-toggle-label]");
  const ladderTotalUnreadBadge = document.querySelector("[data-ladder-total-unread]");
  const ladderLobbyChatRoot = document.querySelector("[data-ladder-lobby-chat-root]");
  const ladderChatRoot = document.querySelector("[data-ladder-chat-root]");
  const ladderUnreadBadges = {
    lobby: document.querySelector('[data-ladder-unread-badge="lobby"]'),
    match: document.querySelector('[data-ladder-unread-badge="match"]'),
  };
  const ladderOnlineCount = document.querySelector("[data-ladder-online-count]");
  const ladderQueue1v1Count = document.querySelector("[data-ladder-queue-1v1]");
  const ladderQueue2v2Count = document.querySelector("[data-ladder-queue-2v2]");
  const ladderActiveMatchCount = document.querySelector("[data-ladder-active-matches]");
  const ladderOnlineList = document.querySelector("[data-ladder-online-list]");
  const ladderMatchAnchor = document.querySelector("[data-ladder-match-anchor]");
  const ladderBoardRoot = document.querySelector("[data-ladder-board-root]");
  const ladderBoardButtons = Array.from(document.querySelectorAll("[data-ladder-board-button]"));
  const ladderBoardPanels = Array.from(document.querySelectorAll("[data-ladder-board-panel]"));
  const ladderBoardEmpty = document.querySelector("[data-ladder-board-empty]");
  const bpReplayRoot = document.querySelector("[data-bp-replay-root]");
  const tournamentForm = document.querySelector(".tournament-form");
  const currentUrl = new URL(window.location.href);
  const bpScrollStorageKey = [
    "bp-scroll-position",
    currentUrl.pathname,
    currentUrl.searchParams.get("tab") || "default",
  ].join(":");
  const bpPrepareWatchStorageKey = [
    "bp-prepare-watch",
    currentUrl.pathname,
    currentUrl.searchParams.get("tab") || "default",
  ].join(":");
  const ladderChatCollapsedStorageKey = ["ladder-chat-collapsed", currentUrl.pathname].join(":");
  const ladderMatchFocusStorageKey = ["ladder-match-focus", currentUrl.pathname].join(":");
  const ladderExpiredFlashStorageKey = ["ladder-expired-flash", currentUrl.pathname].join(":");
  const bpPrepareWatchWindowMs = 15000;

  const initializeCountdowns = (root = document) => {
    const countdownRoot = root instanceof Document || root instanceof Element ? root : document;
    Array.from(countdownRoot.querySelectorAll("[data-countdown-seconds]")).forEach((node) => {
      if (!(node instanceof HTMLElement) || node.dataset.countdownBound === "true") {
        return;
      }

      node.dataset.countdownBound = "true";
      let remaining = Number.parseInt(node.dataset.countdownSeconds || "0", 10);
      const render = () => {
        const safeRemaining = Number.isNaN(remaining) ? 0 : Math.max(0, remaining);
        node.textContent = `${safeRemaining}s`;
        node.classList.toggle("is-expired", safeRemaining === 0);
      };

      render();
      if (remaining <= 0) {
        return;
      }

      const timerId = window.setInterval(() => {
        if (!document.body.contains(node)) {
          window.clearInterval(timerId);
          return;
        }

        remaining = Math.max(0, remaining - 1);
        render();
        if (remaining <= 0) {
          window.clearInterval(timerId);
        }
      }, 1000);
    });
  };

  const bpScrollTargets = () =>
    Array.from(
      document.querySelectorAll(".dashboard-scroll-panel, .table-scroll, [data-scroll-restore-id]"),
    ).filter((node) => node instanceof HTMLElement);

  const persistBpScrollPosition = () => {
    if (!(bpSyncRoot instanceof HTMLElement)) {
      return;
    }
    const scrollingElement = document.scrollingElement || document.documentElement;
    try {
      window.sessionStorage.setItem(
        bpScrollStorageKey,
        JSON.stringify({
          x: scrollingElement?.scrollLeft ?? window.scrollX,
          y: scrollingElement?.scrollTop ?? window.scrollY,
          targets: bpScrollTargets().map((node) => ({
            id: node.dataset.scrollRestoreId || "",
            top: node.scrollTop,
            left: node.scrollLeft,
          })),
          timestamp: Date.now(),
        }),
      );
    } catch {
      // Ignore storage failures so BP interactions keep working.
    }
  };

  const restoreBpScrollPosition = () => {
    if (!(bpSyncRoot instanceof HTMLElement)) {
      return;
    }
    try {
      const rawValue = window.sessionStorage.getItem(bpScrollStorageKey);
      if (!rawValue) {
        return;
      }
      window.sessionStorage.removeItem(bpScrollStorageKey);

      const savedPosition = JSON.parse(rawValue);
      const timestamp = Number(savedPosition?.timestamp || 0);
      const x = Number(savedPosition?.x || 0);
      const y = Number(savedPosition?.y || 0);
      const targets = Array.isArray(savedPosition?.targets) ? savedPosition.targets : [];
      if (Date.now() - timestamp > 15000 || Number.isNaN(x) || Number.isNaN(y)) {
        return;
      }

      const applySavedPosition = () => {
        const scrollingElement = document.scrollingElement || document.documentElement;
        window.scrollTo(x, y);
        if (scrollingElement) {
          scrollingElement.scrollLeft = x;
          scrollingElement.scrollTop = y;
        }
        targets.forEach((targetState) => {
          if (!targetState || typeof targetState !== "object") {
            return;
          }
          const targetId = String(targetState.id || "");
          if (!targetId) {
            return;
          }
          const targetNode = document.querySelector(`[data-scroll-restore-id="${CSS.escape(targetId)}"]`);
          if (!(targetNode instanceof HTMLElement)) {
            return;
          }
          const top = Number(targetState.top || 0);
          const left = Number(targetState.left || 0);
          if (!Number.isNaN(top)) {
            targetNode.scrollTop = top;
          }
          if (!Number.isNaN(left)) {
            targetNode.scrollLeft = left;
          }
        });
      };

      [0, 40, 140, 320, 700].forEach((delayMs) => {
        window.setTimeout(() => {
          window.requestAnimationFrame(applySavedPosition);
        }, delayMs);
      });
    } catch {
      // Ignore malformed saved state and continue with normal load.
    }
  };

  const clearPrepareWatch = () => {
    try {
      window.sessionStorage.removeItem(bpPrepareWatchStorageKey);
    } catch {
      // Ignore storage failures so BP interactions keep working.
    }
  };

  const hasActivePrepareWatch = () => {
    if (!(bpSyncRoot instanceof HTMLElement)) {
      return false;
    }
    try {
      const rawValue = window.sessionStorage.getItem(bpPrepareWatchStorageKey);
      if (!rawValue) {
        return false;
      }
      const savedWatch = JSON.parse(rawValue);
      const timestamp = Number(savedWatch?.timestamp || 0);
      if (Number.isNaN(timestamp) || Date.now() - timestamp > bpPrepareWatchWindowMs) {
        clearPrepareWatch();
        return false;
      }
      return true;
    } catch {
      clearPrepareWatch();
      return false;
    }
  };

  const persistPrepareWatch = (form) => {
    if (!(bpSyncRoot instanceof HTMLElement) || !(form instanceof HTMLFormElement)) {
      return;
    }
    const actionPath = new URL(form.action || currentUrl.href, currentUrl.href).pathname;
    if (!/\/matches\/\d+\/prepare$/.test(actionPath)) {
      return;
    }
    try {
      window.sessionStorage.setItem(
        bpPrepareWatchStorageKey,
        JSON.stringify({
          timestamp: Date.now(),
        }),
      );
    } catch {
      // Ignore storage failures so BP interactions keep working.
    }
  };

  if (tournamentForm instanceof HTMLFormElement) {
    const formatSelect = tournamentForm.querySelector('select[name="format_type"]');
    const stageModeSelect = tournamentForm.querySelector('select[name="stage_mode"]');
    const stage2FormatSelect = tournamentForm.querySelector('select[name="stage2_format_type"]');
    const visibilityGroups = Array.from(tournamentForm.querySelectorAll("[data-format-visibility]"));

    const syncTournamentFormatVisibility = () => {
      const activeFormats = new Set();
      if (formatSelect instanceof HTMLSelectElement && formatSelect.value) {
        activeFormats.add(formatSelect.value);
      }
      if (
        stageModeSelect instanceof HTMLSelectElement &&
        stageModeSelect.value === "double_stage" &&
        stage2FormatSelect instanceof HTMLSelectElement &&
        stage2FormatSelect.value
      ) {
        activeFormats.add(stage2FormatSelect.value);
      }

      visibilityGroups.forEach((group) => {
        if (!(group instanceof HTMLElement)) {
          return;
        }
        group.hidden = !activeFormats.has(group.dataset.formatVisibility || "");
      });
    };

    syncTournamentFormatVisibility();
    [formatSelect, stageModeSelect, stage2FormatSelect].forEach((field) => {
      field?.addEventListener("change", syncTournamentFormatVisibility);
    });
  }

  initializeCountdowns();

  const showPendingLadderExpiredAlert = () => {
    if (!(ladderExpiredAlert instanceof HTMLElement)) {
      return;
    }
    try {
      const pending = window.sessionStorage.getItem(ladderExpiredFlashStorageKey);
      if (!pending) {
        return;
      }
      window.sessionStorage.removeItem(ladderExpiredFlashStorageKey);
      ladderExpiredAlert.hidden = false;
    } catch {
      // Ignore storage failures so the page can continue rendering.
    }
  };

  showPendingLadderExpiredAlert();

  let activeChatPanel = ladderChatDock instanceof HTMLElement ? ladderChatDock.dataset.defaultChatPanel || "lobby" : "lobby";
  let ladderChatCollapsed = false;
  const unreadCounts = { lobby: 0, match: 0 };
  const lastKnownChatMessageId = { lobby: 0, match: 0 };

  const syncUnreadBadges = () => {
    Object.entries(ladderUnreadBadges).forEach(([channel, node]) => {
      if (!(node instanceof HTMLElement)) {
        return;
      }
      const count = unreadCounts[channel] || 0;
      node.textContent = `${count}`;
      node.hidden = count <= 0;
    });

    if (ladderTotalUnreadBadge instanceof HTMLElement) {
      const totalUnread = Object.values(unreadCounts).reduce((sum, value) => sum + value, 0);
      ladderTotalUnreadBadge.textContent = `${totalUnread}`;
      ladderTotalUnreadBadge.hidden = totalUnread <= 0;
    }
  };

  const markChatPanelRead = (panelName) => {
    if (!(panelName in unreadCounts)) {
      return;
    }
    unreadCounts[panelName] = 0;
    syncUnreadBadges();
  };

  const persistChatCollapsedState = () => {
    try {
      window.sessionStorage.setItem(ladderChatCollapsedStorageKey, ladderChatCollapsed ? "1" : "0");
    } catch {
      // Ignore storage failures so the chat dock remains usable.
    }
  };

  const updateChatDockLayout = () => {
    if (!(ladderChatDock instanceof HTMLElement)) {
      return;
    }
    ladderChatDock.classList.toggle("is-collapsed", ladderChatCollapsed);
    if (ladderChatToggleLabel instanceof HTMLElement) {
      ladderChatToggleLabel.textContent = ladderChatCollapsed ? "展开" : "收起";
    }
    ladderChatTabs.forEach((tab) => {
      if (!(tab instanceof HTMLButtonElement)) {
        return;
      }
      const active = tab.dataset.ladderChatTab === activeChatPanel;
      tab.classList.toggle("is-active-filter", active && !ladderChatCollapsed);
    });
    ladderChatPanels.forEach((panel) => {
      if (!(panel instanceof HTMLElement)) {
        return;
      }
      const active = !ladderChatCollapsed && panel.dataset.ladderChatPanel === activeChatPanel;
      panel.classList.toggle("is-active", active);
    });
  };

  const activateChatPanel = (panelName) => {
    activeChatPanel = panelName;
    if (!ladderChatCollapsed) {
      markChatPanelRead(panelName);
    }
    updateChatDockLayout();
  };

  const toggleChatDock = () => {
    ladderChatCollapsed = !ladderChatCollapsed;
    if (!ladderChatCollapsed) {
      markChatPanelRead(activeChatPanel);
    }
    updateChatDockLayout();
    persistChatCollapsedState();
  };

  const updateChatUnread = (channel, messages) => {
    if (!(channel in unreadCounts) || !Array.isArray(messages)) {
      return;
    }

    const numericIds = messages
      .map((message) => Number.parseInt(`${message?.id || 0}`, 10))
      .filter((value) => Number.isFinite(value) && value > 0);
    const latestId = numericIds.length ? numericIds[numericIds.length - 1] : 0;
    const previousId = lastKnownChatMessageId[channel] || 0;
    const newCount = previousId > 0 ? numericIds.filter((value) => value > previousId).length : 0;

    if (newCount > 0 && (ladderChatCollapsed || activeChatPanel !== channel)) {
      unreadCounts[channel] += newCount;
    }
    lastKnownChatMessageId[channel] = latestId;

    if (!ladderChatCollapsed && activeChatPanel === channel) {
      unreadCounts[channel] = 0;
    }
    syncUnreadBadges();
  };

  if (ladderChatDock instanceof HTMLElement && ladderChatTabs.length && ladderChatPanels.length) {
    try {
      ladderChatCollapsed = window.sessionStorage.getItem(ladderChatCollapsedStorageKey) === "1";
    } catch {
      ladderChatCollapsed = false;
    }

    updateChatDockLayout();
    syncUnreadBadges();

    if (ladderChatToggle instanceof HTMLButtonElement) {
      ladderChatToggle.addEventListener("click", toggleChatDock);
    }

    ladderChatTabs.forEach((tab) => {
      if (!(tab instanceof HTMLButtonElement) || tab.disabled) {
        return;
      }
      tab.addEventListener("click", () => {
        activateChatPanel(tab.dataset.ladderChatTab || "lobby");
      });
    });
  }

  if (ladderBoardRoot instanceof HTMLElement && ladderBoardButtons.length && ladderBoardPanels.length) {
    let activeBoardPanel = ladderBoardRoot.dataset.defaultLadderBoard || "";

    const renderBoardPanels = () => {
      ladderBoardButtons.forEach((button) => {
        if (!(button instanceof HTMLButtonElement)) {
          return;
        }
        const active = button.dataset.ladderBoardButton === activeBoardPanel;
        button.classList.toggle("is-active-filter", active);
      });
      ladderBoardPanels.forEach((panel) => {
        if (!(panel instanceof HTMLElement)) {
          return;
        }
        const active = panel.dataset.ladderBoardPanel === activeBoardPanel;
        panel.classList.toggle("is-active", active);
      });
      if (ladderBoardEmpty instanceof HTMLElement) {
        ladderBoardEmpty.hidden = Boolean(activeBoardPanel);
      }
    };

    ladderBoardButtons.forEach((button) => {
      if (!(button instanceof HTMLButtonElement)) {
        return;
      }
      button.addEventListener("click", () => {
        const panelName = button.dataset.ladderBoardButton || "";
        activeBoardPanel = activeBoardPanel === panelName ? "" : panelName;
        renderBoardPanels();
      });
    });

    renderBoardPanels();
  }

  const scrollLadderMatchIntoView = () => {
    if (!(ladderMatchAnchor instanceof HTMLElement)) {
      return;
    }

    const applyScroll = () => {
      const rect = ladderMatchAnchor.getBoundingClientRect();
      const targetTop = window.scrollY + rect.top - Math.max(0, (window.innerHeight - rect.height) / 2);
      window.scrollTo({ top: Math.max(0, targetTop), behavior: "smooth" });
    };

    [0, 100, 260, 520].forEach((delayMs) => {
      window.setTimeout(() => {
        window.requestAnimationFrame(applyScroll);
      }, delayMs);
    });
  };

  const restorePendingLadderMatchFocus = () => {
    try {
      const pending = window.sessionStorage.getItem(ladderMatchFocusStorageKey);
      if (!pending || !(ladderMatchAnchor instanceof HTMLElement)) {
        return;
      }
      window.sessionStorage.removeItem(ladderMatchFocusStorageKey);
      scrollLadderMatchIntoView();
    } catch {
      // Ignore storage failures so the page still loads normally.
    }
  };

  restorePendingLadderMatchFocus();
  window.addEventListener("load", restorePendingLadderMatchFocus, { once: true });

  if (bpSyncRoot instanceof HTMLElement) {
    let syncUrl = bpSyncRoot.dataset.bpSyncUrl || "";
    const ladderLive = bpSyncRoot.dataset.ladderLive === "true";
    const partialUrl = bpSyncRoot.dataset.bpPartialUrl || "";
    const partialTargetName = bpSyncRoot.dataset.bpPartialTarget || "";
    let currentMatchId = Number.parseInt(bpSyncRoot.dataset.ladderCurrentMatchId || "0", 10);
    let currentMatchState = bpSyncRoot.dataset.ladderCurrentMatchState || "";
    let syncKey = bpSyncRoot.dataset.bpSyncKey || "";
    const intervalMs = Number.parseInt(bpSyncRoot.dataset.bpSyncInterval || "2000", 10);
    let refreshPending = false;
    let suspendUntil = 0;

    const buildPartialRequestUrl = () => {
      if (!partialUrl) {
        return "";
      }

      const requestUrl = new URL(partialUrl, currentUrl.href);
      currentUrl.searchParams.forEach((value, key) => {
        if (!requestUrl.searchParams.has(key)) {
          requestUrl.searchParams.set(key, value);
        }
      });
      return requestUrl.toString();
    };

    const preserveFormInputs = (oldTarget, newTarget) => {
      // 将旧 DOM 中 checkbox/radio 的选中状态复制到新 DOM
      if (!(oldTarget instanceof HTMLElement) || !(newTarget instanceof HTMLElement)) {
        return;
      }
      const oldInputs = oldTarget.querySelectorAll("input[type=checkbox], input[type=radio]");
      oldInputs.forEach((oldInput) => {
        if (!(oldInput instanceof HTMLInputElement) || !oldInput.name) {
          return;
        }
        const selector = `input[name="${CSS.escape(oldInput.name)}"][value="${CSS.escape(oldInput.value)}"]`;
        const newInput = newTarget.querySelector(selector);
        if (newInput instanceof HTMLInputElement) {
          newInput.checked = oldInput.checked;
        }
      });
    };

    const refreshBpPartial = async () => {
      if (!partialUrl || !partialTargetName) {
        return null;
      }

      const requestUrl = buildPartialRequestUrl();
      if (!requestUrl) {
        return null;
      }

      const response = await fetch(requestUrl, {
        method: "GET",
        credentials: "same-origin",
        cache: "no-store",
        headers: {
          Accept: "text/html",
        },
      });
      if (!response.ok) {
        return null;
      }

      const markup = await response.text();
      const parsed = new DOMParser().parseFromString(markup, "text/html");
      const selector = `[data-bp-partial-target="${CSS.escape(partialTargetName)}"]`;
      const nextTarget = parsed.querySelector(selector);
      const currentTarget = document.querySelector(selector);
      if (!(nextTarget instanceof HTMLElement) || !(currentTarget instanceof HTMLElement)) {
        return null;
      }

      persistBpScrollPosition();
      // 保留表单输入状态（防止 Ajax 刷新时用户正在填写的勾选框丢失）
      preserveFormInputs(currentTarget, nextTarget);
      currentTarget.replaceWith(nextTarget);
      initializeCountdowns(nextTarget);
      restoreBpScrollPosition();

      return {
        nextSyncUrl: nextTarget.dataset.bpNextSyncUrl || "",
        nextSyncKey: nextTarget.dataset.bpNextSyncKey || "",
        nextMatchId: Number.parseInt(nextTarget.dataset.ladderCurrentMatchId || "0", 10),
        nextMatchState: nextTarget.dataset.ladderCurrentMatchState || "",
      };
    };

    const renderLadderChat = (root, messages, emptyText, channel) => {
      if (!(root instanceof HTMLElement)) {
        return;
      }

      const chatList = root.querySelector("[data-ladder-chat-list]");
      if (!(chatList instanceof HTMLElement) || !Array.isArray(messages)) {
        return;
      }

      const signature = JSON.stringify(
        messages.map((message) => [message?.id, message?.time, message?.sender, message?.text]),
      );
      if (chatList.dataset.chatSignature === signature) {
        return;
      }
      chatList.dataset.chatSignature = signature;

      const fragment = document.createDocumentFragment();
      if (!messages.length) {
        const emptyNode = document.createElement("p");
        emptyNode.className = "muted-copy";
        emptyNode.dataset.ladderChatEmpty = "true";
        emptyNode.textContent = emptyText;
        fragment.append(emptyNode);
      } else {
        messages.forEach((message) => {
          const item = document.createElement("article");
          item.className = "ladder-chat-entry";

          const meta = document.createElement("div");
          meta.className = "ladder-chat-meta";

          const sender = document.createElement("strong");
          sender.textContent = `${message?.sender || "未知用户"}`;
          meta.append(sender);

          const time = document.createElement("span");
          time.textContent = `${message?.time || ""}`;
          meta.append(time);

          const text = document.createElement("p");
          text.textContent = `${message?.text || ""}`;

          item.append(meta, text);
          fragment.append(item);
        });
      }

      chatList.replaceChildren(fragment);
      chatList.scrollTop = chatList.scrollHeight;
      updateChatUnread(channel, messages);
    };

    const renderLadderLobby = (lobby) => {
      if (!ladderLive || !lobby || typeof lobby !== "object") {
        return;
      }

      const bindings = [
        [ladderOnlineCount, lobby.online_count],
        [ladderQueue1v1Count, lobby.queue_1v1_players],
        [ladderQueue2v2Count, lobby.queue_2v2_players],
        [ladderActiveMatchCount, lobby.active_matches],
      ];

      bindings.forEach(([node, value]) => {
        if (node instanceof HTMLElement && Number.isFinite(Number(value))) {
          node.textContent = `${Number(value)}`;
        }
      });

      if (ladderOnlineList instanceof HTMLElement) {
        const onlineUsers = Array.isArray(lobby.online_users) ? lobby.online_users : [];
        const signature = JSON.stringify(
          onlineUsers.map((entry) => [entry?.username, entry?.game_nickname, entry?.status_label]),
        );
        if (ladderOnlineList.dataset.listSignature !== signature) {
          ladderOnlineList.dataset.listSignature = signature;
          const fragment = document.createDocumentFragment();
          if (!onlineUsers.length) {
            const emptyNode = document.createElement("p");
            emptyNode.className = "muted-copy";
            emptyNode.textContent = "当前还没有停留在天梯页面的在线用户。";
            fragment.append(emptyNode);
          } else {
            onlineUsers.forEach((entry) => {
              const item = document.createElement("article");
              item.className = "ladder-online-entry";

              const meta = document.createElement("div");
              meta.className = "ladder-online-meta";

              const username = document.createElement("strong");
              username.textContent = `${entry?.username || "未知用户"}`;
              meta.append(username);

              const nickname = document.createElement("span");
              nickname.textContent = entry?.game_nickname || "未绑定游戏昵称";
              meta.append(nickname);

              const status = document.createElement("span");
              status.className = "status-badge";
              status.textContent = `${entry?.status_label || "大厅中"}`;

              item.append(meta, status);
              fragment.append(item);
            });
          }
          ladderOnlineList.replaceChildren(fragment);
        }
      }
    };

    const renderLadderMatchChat = (currentMatchId, messages) => {
      if (!ladderLive || !(ladderChatRoot instanceof HTMLElement)) {
        return;
      }

      const expectedMatchId = Number.parseInt(ladderChatRoot.dataset.matchId || "0", 10);
      const actualMatchId = Number.parseInt(`${currentMatchId || 0}`, 10);
      if (!expectedMatchId || expectedMatchId !== actualMatchId) {
        return;
      }

      renderLadderChat(ladderChatRoot, messages, "暂时还没有聊天消息。", "match");
    };

    if ("scrollRestoration" in window.history) {
      window.history.scrollRestoration = "manual";
    }
    restoreBpScrollPosition();
    window.addEventListener("load", restoreBpScrollPosition, { once: true });
    window.addEventListener("pagehide", persistBpScrollPosition);
    window.addEventListener("beforeunload", persistBpScrollPosition);

    document.addEventListener("submit", (event) => {
      if (!(event.target instanceof HTMLFormElement)) {
        return;
      }
      if (!event.target.closest(".prepare-match-panel, .prep-mode-card, .bracket-prep-observer, .prep-entry-card")) {
        return;
      }
      persistPrepareWatch(event.target);
      persistBpScrollPosition();
      const actionPath = new URL(event.target.action || currentUrl.href, currentUrl.href).pathname;
      const isReplaySubmission = /\/tournaments\/\d+\/matches\/\d+\/replay(?:\/(?:skip|manual-result|admin-result))?$/.test(
        actionPath,
      );
      suspendUntil = Date.now() + (isReplaySubmission ? 120000 : 5000);
    });

    const pollBpSync = async () => {
      if (!syncUrl || refreshPending || document.hidden || Date.now() < suspendUntil) {
        return;
      }

      try {
        const response = await fetch(syncUrl, {
          method: "GET",
          credentials: "same-origin",
          cache: "no-store",
          headers: {
            Accept: "application/json",
          },
        });
        if (!response.ok) {
          return;
        }

        const payload = await response.json();
        if (ladderLive) {
          renderLadderLobby(payload.lobby);
          renderLadderChat(ladderLobbyChatRoot, payload.lobby_chat_messages, "大厅公共频道暂时还没有聊天消息。", "lobby");
          renderLadderMatchChat(payload.current_match_id, payload.match_chat_messages || payload.chat_messages);
        }
        if (
          currentMatchState === "pending_accept" &&
          currentMatchId > 0 &&
          Number.parseInt(`${payload.current_match_id || 0}`, 10) === 0
        ) {
          try {
            window.sessionStorage.setItem(ladderExpiredFlashStorageKey, "1");
          } catch {
            // Ignore storage failures so polling can continue.
          }
        }
        if (!currentMatchId && Number.parseInt(`${payload.current_match_id || 0}`, 10) > 0) {
          try {
            window.sessionStorage.setItem(ladderMatchFocusStorageKey, `${payload.current_match_id}`);
          } catch {
            // Ignore storage failures so matching still proceeds.
          }
        }
        if (payload.sync_key && payload.sync_key !== syncKey) {
          clearPrepareWatch();
          const nextMatchId = Number.parseInt(`${payload.current_match_id || 0}`, 10);
          const canRefreshPartial = Boolean(partialUrl && partialTargetName) && nextMatchId === currentMatchId;
          if (canRefreshPartial) {
            let partialState = null;
            refreshPending = true;
            try {
              partialState = await refreshBpPartial();
            } finally {
              refreshPending = false;
            }
            if (partialState) {
              syncUrl = partialState.nextSyncUrl;
              syncKey = partialState.nextSyncKey || payload.sync_key;
              currentMatchId = Number.isFinite(partialState.nextMatchId) ? partialState.nextMatchId : currentMatchId;
              currentMatchState = partialState.nextMatchState || currentMatchState;
              bpSyncRoot.dataset.bpSyncUrl = syncUrl;
              bpSyncRoot.dataset.bpSyncKey = syncKey;
              bpSyncRoot.dataset.ladderCurrentMatchId = `${currentMatchId || ""}`;
              bpSyncRoot.dataset.ladderCurrentMatchState = currentMatchState;
              return;
            }
          }

          refreshPending = true;
          persistBpScrollPosition();
          window.location.reload();
          return;
        }
        currentMatchId = Number.parseInt(`${payload.current_match_id || currentMatchId || 0}`, 10);
        currentMatchState = Number.parseInt(`${payload.current_match_id || 0}`, 10) > 0 ? currentMatchState : "";
      } catch {
        // Ignore transient polling failures and keep the current page interactive.
      }
    };

    if (hasActivePrepareWatch()) {
      [0, 250, 600, 1100, 1800, 2700, 3800, 5200, 7000, 9000].forEach((delayMs) => {
        window.setTimeout(() => {
          if (hasActivePrepareWatch()) {
            pollBpSync();
          }
        }, delayMs);
      });
    }
    window.setInterval(pollBpSync, Math.max(1000, intervalMs));
  }

  if (bpReplayRoot instanceof HTMLElement) {
    const payloadNode = bpReplayRoot.querySelector("[data-bp-replay-payload]");
    const titleNode = bpReplayRoot.querySelector("[data-bp-replay-title]");
    const detailNode = bpReplayRoot.querySelector("[data-bp-replay-detail]");
    const summaryNode = bpReplayRoot.querySelector("[data-bp-replay-summary]");
    const indexNode = bpReplayRoot.querySelector("[data-bp-replay-index]");
    const mapNode = bpReplayRoot.querySelector("[data-bp-replay-map]");
    const sideNode = bpReplayRoot.querySelector("[data-bp-replay-side]");
    const winnerNode = bpReplayRoot.querySelector("[data-bp-replay-winner]");
    const commanderNode = bpReplayRoot.querySelector("[data-bp-replay-commander]");
    const artNode = bpReplayRoot.querySelector("[data-bp-replay-art]");
    const timelineNode = bpReplayRoot.querySelector("[data-bp-replay-timeline]");
    const controlButtons = Array.from(bpReplayRoot.querySelectorAll("[data-bp-replay-action]"));

    let replayPayload = null;
    try {
      replayPayload = payloadNode ? JSON.parse(payloadNode.textContent || "null") : null;
    } catch {
      replayPayload = null;
    }

    const frames = Array.isArray(replayPayload?.frames) ? replayPayload.frames : [];
    let activeFrameIndex = 0;
    let autoplayTimerId = 0;

    const stopAutoplay = () => {
      if (autoplayTimerId) {
        window.clearInterval(autoplayTimerId);
        autoplayTimerId = 0;
      }
      controlButtons.forEach((button) => {
        if (!(button instanceof HTMLButtonElement)) {
          return;
        }
        if (button.dataset.bpReplayAction === "play") {
          button.textContent = "自动播放";
        }
      });
    };

    const buildTimeline = () => {
      if (!(timelineNode instanceof HTMLElement)) {
        return;
      }
      const fragment = document.createDocumentFragment();
      frames.forEach((frame, index) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "bp-replay-tick";
        button.dataset.frameIndex = `${index}`;
        button.innerHTML = `<strong>G${Number(frame?.game_number || 0)}</strong><span>${frame?.title || "步骤"}</span>`;
        button.addEventListener("click", () => {
          stopAutoplay();
          activeFrameIndex = index;
          renderFrame();
        });
        fragment.append(button);
      });
      timelineNode.replaceChildren(fragment);
    };

    const renderFrame = () => {
      if (!frames.length) {
        return;
      }
      const frame = frames[activeFrameIndex] || frames[0];
      if (titleNode instanceof HTMLElement) {
        titleNode.textContent = `${frame?.title || ""}`;
      }
      if (detailNode instanceof HTMLElement) {
        detailNode.textContent = `${frame?.detail || ""}`;
      }
      if (summaryNode instanceof HTMLElement) {
        const summaryLines = Array.isArray(frame?.summary_lines) ? frame.summary_lines : [];
        summaryNode.hidden = summaryLines.length === 0;
        summaryNode.replaceChildren(
          ...summaryLines.map((line) => {
            const item = document.createElement("div");
            item.className = "bp-replay-summary-line";
            item.textContent = `${line || ""}`;
            return item;
          }),
        );
      }
      if (indexNode instanceof HTMLElement) {
        indexNode.textContent = `步骤 ${activeFrameIndex + 1} / ${frames.length}`;
      }
      if (mapNode instanceof HTMLElement) {
        const hasMap = Boolean(frame?.map_name);
        mapNode.hidden = !hasMap;
        mapNode.textContent = hasMap ? `地图：${frame.map_name}` : "";
      }
      if (sideNode instanceof HTMLElement) {
        const hasSide = Boolean(frame?.side_label);
        sideNode.hidden = !hasSide;
        sideNode.textContent = hasSide ? `${frame.side_label}` : "";
      }
      if (winnerNode instanceof HTMLElement) {
        const hasWinner = Boolean(frame?.winner_name);
        winnerNode.hidden = !hasWinner;
        winnerNode.textContent = hasWinner ? `胜者：${frame.winner_name}` : "";
      }
      if (commanderNode instanceof HTMLElement) {
        const hasCommander = Boolean(frame?.commander_name);
        commanderNode.hidden = !hasCommander;
        commanderNode.textContent = hasCommander ? `指挥官：${frame.commander_name}` : "";
      }
      if (artNode instanceof HTMLElement) {
        const imageUrl = `${frame?.image_url || ""}`;
        artNode.hidden = !imageUrl;
        artNode.style.backgroundImage = imageUrl ? `url("${imageUrl}")` : "";
      }
      if (timelineNode instanceof HTMLElement) {
        Array.from(timelineNode.querySelectorAll(".bp-replay-tick")).forEach((node, index) => {
          if (node instanceof HTMLButtonElement) {
            node.classList.toggle("is-active", index === activeFrameIndex);
          }
        });
      }
    };

    controlButtons.forEach((button) => {
      if (!(button instanceof HTMLButtonElement)) {
        return;
      }
      button.addEventListener("click", () => {
        const action = button.dataset.bpReplayAction || "";
        if (!frames.length) {
          return;
        }
        if (action === "prev") {
          stopAutoplay();
          activeFrameIndex = Math.max(0, activeFrameIndex - 1);
          renderFrame();
          return;
        }
        if (action === "next") {
          stopAutoplay();
          activeFrameIndex = Math.min(frames.length - 1, activeFrameIndex + 1);
          renderFrame();
          return;
        }
        if (action === "play") {
          if (autoplayTimerId) {
            stopAutoplay();
            return;
          }
          if (activeFrameIndex >= frames.length - 1) {
            activeFrameIndex = 0;
            renderFrame();
          }
          button.textContent = "暂停";
          autoplayTimerId = window.setInterval(() => {
            if (activeFrameIndex >= frames.length - 1) {
              stopAutoplay();
              renderFrame();
              return;
            }
            activeFrameIndex += 1;
            renderFrame();
            if (activeFrameIndex >= frames.length - 1) {
              stopAutoplay();
            }
          }, 2200);
        }
      });
    });

    buildTimeline();
    renderFrame();
  }

  if (!tabs.length || !panels.length) {
    return;
  }

  const activate = (panelName) => {
    tabs.forEach((tab) => {
      tab.classList.toggle("is-active", tab.dataset.panel === panelName);
    });
    panels.forEach((panel) => {
      panel.classList.toggle("is-active", panel.dataset.panel === panelName);
    });
  };

  activate(authMode);

  tabs.forEach((tab) => {
    tab.addEventListener("click", () => activate(tab.dataset.panel));
  });
});