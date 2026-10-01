const input = document.getElementById("messageInput");
const button = document.getElementById("sendButton");
const chat = document.getElementById("chat");
const conversation = document.getElementById("conversation");
const welcome = document.getElementById("welcome");
const newChatButton = document.getElementById("newChatButton");
const conversationTitle = document.getElementById("conversationTitle");
const conversationList = document.getElementById("conversationList");
const status = document.getElementById("status");
const statusText = document.getElementById("statusText");
const sendLabel = document.getElementById("sendLabel");
const menuButton = document.getElementById("menuButton");
const sidebar = document.getElementById("sidebar");
const mainContent = document.getElementById("mainContent");
const mobileLayout = window.matchMedia("(max-width: 760px)");
const storageNotice = document.getElementById("storageNotice");

// SQLite owns every conversation. The browser tracks only the selected chat.
let currentConversationId = null;
let savedConversations = [];
let isSending = false;
let isBusy = true;
let followOutput = true;
const searchInput = document.getElementById("chatSearch");
const clearSearchButton = document.getElementById("clearSearch");
const searchPanel = document.getElementById("searchPanel");
const searchResults = document.getElementById("searchResults");
const searchStatus = document.getElementById("searchStatus");
const actionDialog = document.getElementById("conversationDialog");
const renameInput = document.getElementById("renameInput");
let dialogAction = null;
let searchTimer;
let searchController;
let searchVersion = 0;
let lastSearchResults = [];
let titleTimer;
let titlePolls = 0;

function showStorageNotice(text) {
    storageNotice.textContent = text;
    storageNotice.hidden = !text;
}

async function requireSuccess(response, action) {
    if (response.ok) return;
    let detail = "";
    try {
        const data = await response.json();
        if (typeof data.detail === "string") detail = " " + data.detail;
    } catch {
        // Some network/server failures do not provide a JSON error body.
    }
    throw new Error(action + " (HTTP " + response.status + ")." + detail);
}

function clearConversationView() {
    chat.replaceChildren();
    chat.hidden = true;
    welcome.hidden = false;
    conversationTitle.textContent = "New conversation";
}

function validConversation(item) {
    return item && Number.isSafeInteger(item.id) && item.id > 0 &&
        typeof item.title === "string";
}

async function getConversations() {
    const response = await fetch("/conversations", { cache: "no-store" });
    await requireSuccess(response, "Couldn't load saved chats");
    const data = await response.json();
    if (!Array.isArray(data.conversations) || !data.conversations.every(validConversation)) {
        throw new Error("The server returned an invalid conversation list.");
    }
    return data.conversations;
}

function renderConversationList(conversations = savedConversations) {
    conversationList.replaceChildren();
    let previousGroup;
    for (const item of new Map(conversations.map(item => [item.id, item])).values()) {
        const group = dateGroup(item.updated_at);
        if (group !== previousGroup) {
            const heading = document.createElement("h3");
            heading.className = "date-group";
            heading.textContent = group;
            conversationList.appendChild(heading);
            previousGroup = group;
        }
        const row = document.createElement("div");
        row.className = "conversation-row";
        const itemButton = document.createElement("button");
        itemButton.type = "button";
        itemButton.className = "conversation-item";
        itemButton.dataset.conversationId = String(item.id);
        itemButton.textContent = item.title;
        itemButton.title = item.title;
        itemButton.disabled = isBusy;
        if (item.id === currentConversationId) itemButton.setAttribute("aria-current", "page");
        itemButton.addEventListener("click", () => openConversation(item.id));
        const menu = document.createElement("details");
        menu.className = "conversation-menu";
        const toggle = document.createElement("summary");
        toggle.textContent = "⋯";
        toggle.setAttribute("aria-label", "Actions for " + item.title);
        toggle.title = "Conversation actions";
        toggle.addEventListener("click", event => {
            if (isBusy) { event.preventDefault(); return; }
            conversationList.querySelectorAll("details").forEach(other => {
                if (other !== menu) other.open = false;
            });
        });
        const actions = document.createElement("div");
        actions.className = "conversation-actions";
        for (const action of ["Rename", "Delete"]) {
            const control = document.createElement("button");
            control.type = "button";
            control.textContent = action;
            control.disabled = isBusy;
            control.addEventListener("click", () => showConversationAction(action, item));
            actions.appendChild(control);
        }
        menu.append(toggle, actions);
        row.append(itemButton, menu);
        conversationList.appendChild(row);
    }
    if (!conversations.length) {
        const empty = document.createElement("p");
        empty.className = "conversation-list-empty";
        empty.textContent = "Your saved chats will appear here.";
        conversationList.appendChild(empty);
    }
}

async function refreshSidebar() {
    savedConversations = await getConversations();
    renderConversationList();
    const selected = savedConversations.find(item => item.id === currentConversationId);
    if (selected) conversationTitle.textContent = selected.title;
    scheduleTitleRefresh();
    return savedConversations;
}

function dateGroup(timestamp, now = new Date()) {
    // SQLite timestamps are UTC; group by calendar dates in the browser's zone.
    const date = new Date(timestamp.replace(" ", "T") + "Z");
    const day = value => Date.UTC(value.getFullYear(), value.getMonth(), value.getDate());
    const age = Math.round((day(now) - day(date)) / 86400000);
    if (age <= 0) return "Today";
    if (age === 1) return "Yesterday";
    if (age <= 7) return "Previous 7 Days";
    if (age <= 30) return "Previous 30 Days";
    return "Older";
}

function scheduleTitleRefresh() {
    clearTimeout(titleTimer);
    if (titlePolls >= 30 || !savedConversations.some(item => ["pending", "generating"].includes(item.title_source))) return;
    titleTimer = setTimeout(async () => {
        titlePolls++;
        // Do not replace focused menu controls or an open dialog.
        if (isBusy || actionDialog.open || conversationList.querySelector("details[open]") ||
            conversationList.contains(document.activeElement)) {
            scheduleTitleRefresh();
            return;
        }
        try { await refreshSidebar(); } catch { /* Optional title will appear on next refresh. */ }
    }, 1000);
}

function showConversationAction(action, item) {
    if (isBusy) return;
    dialogAction = { action, item };
    conversationList.querySelectorAll("details").forEach(menu => { menu.open = false; });
    const deleting = action === "Delete";
    document.getElementById("dialogTitle").textContent = deleting ? "Delete this chat?" : "Rename chat";
    document.getElementById("dialogDescription").textContent = deleting
        ? 'Delete “' + item.title + '” and all its messages? This cannot be undone.'
        : "Choose a short title (up to 60 characters).";
    document.getElementById("renameLabel").hidden = deleting;
    renameInput.hidden = deleting;
    renameInput.required = !deleting;
    renameInput.value = item.title;
    document.getElementById("dialogError").textContent = "";
    document.getElementById("confirmAction").textContent = deleting ? "Delete chat" : "Save title";
    actionDialog.showModal();
    if (deleting) document.getElementById("cancelAction").focus();
    else { renameInput.focus(); renameInput.select(); }
}

document.getElementById("cancelAction").addEventListener("click", () => actionDialog.close());
actionDialog.addEventListener("cancel", event => { if (isBusy) event.preventDefault(); });
document.getElementById("conversationActionForm").addEventListener("submit", async event => {
    event.preventDefault();
    if (isBusy || !dialogAction) return;
    const { action, item } = dialogAction;
    const deleting = action === "Delete";
    const title = renameInput.value.trim().replace(/\s+/g, " ");
    if (!deleting && !title) {
        document.getElementById("dialogError").textContent = "Enter a title.";
        return;
    }
    setBusy("updating");
    const controls = actionDialog.querySelectorAll("button, input");
    controls.forEach(control => { control.disabled = true; });
    try {
        const response = await fetch("/conversations/" + item.id, deleting ? { method: "DELETE" } : {
            method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ title })
        });
        await requireSuccess(response, deleting ? "Couldn't delete the chat" : "Couldn't rename the chat");
        const deletedActive = deleting && currentConversationId === item.id;
        if (deleting) savedConversations = savedConversations.filter(saved => saved.id !== item.id);
        else savedConversations = savedConversations.map(saved => saved.id === item.id ? { ...saved, title, title_source: "manual" } : saved);
        if (deletedActive) { currentConversationId = null; clearConversationView(); input.value = ""; }
        renderConversationList();
        // The mutation succeeded even if a subsequent refresh fails.
        actionDialog.close();
        await refreshSidebar();
        if (deletedActive && savedConversations.length) {
            const nextId = savedConversations[0].id;
            const messages = await getConversationMessages(nextId);
            currentConversationId = nextId;
            redrawMessages(messages);
            renderConversationList();
        }
        if (searchInput.value.trim()) await runSearch();
    } catch (error) {
        if (actionDialog.open) document.getElementById("dialogError").textContent = error.message;
        else showStorageNotice(error.message + " Refresh to load the latest chat list.");
    } finally {
        controls.forEach(control => { control.disabled = false; });
        setBusy();
        if (!actionDialog.open) { setSidebar(false); input.focus({ preventScroll: true }); }
    }
});

function highlightText(element, value, query) {
    // Build literal text and <mark> nodes; never interpret search content as HTML.
    const text = value || "";
    const needle = query.toLocaleLowerCase();
    const folded = text.toLocaleLowerCase();
    let position = 0;
    let match;
    while (needle && (match = folded.indexOf(needle, position)) !== -1) {
        element.append(document.createTextNode(text.slice(position, match)));
        const mark = document.createElement("mark");
        mark.textContent = text.slice(match, match + query.length);
        element.append(mark);
        position = match + query.length;
    }
    element.append(document.createTextNode(text.slice(position)));
}

function renderSearchResults(results = lastSearchResults) {
    lastSearchResults = results;
    searchResults.replaceChildren();
    for (const item of results) {
        const hit = document.createElement("button");
        hit.type = "button";
        hit.className = "search-hit";
        hit.dataset.conversationId = String(item.conversation_id);
        hit.disabled = isBusy;
        if (item.conversation_id === currentConversationId) hit.setAttribute("aria-current", "page");
        const title = document.createElement("strong");
        title.title = item.title;
        highlightText(title, item.title, searchInput.value.trim());
        const source = document.createElement("span");
        source.className = "search-source";
        source.textContent = item.message_id ? (item.role === "user" ? "You" : "Assistant") + " · message #" + item.message_id : "Title match";
        const snippet = document.createElement("span");
        highlightText(snippet, item.snippet, searchInput.value.trim());
        hit.append(title, source, snippet);
        hit.addEventListener("click", () => openConversation(item.conversation_id));
        searchResults.appendChild(hit);
    }
}

async function runSearch() {
    clearTimeout(searchTimer);
    searchController?.abort();
    const version = ++searchVersion;
    const query = searchInput.value.trim();
    clearSearchButton.hidden = !query;
    searchPanel.hidden = !query;
    conversationList.hidden = Boolean(query);
    if (!query) { lastSearchResults = []; searchResults.replaceChildren(); return; }
    searchStatus.textContent = "Searching…";
    searchResults.replaceChildren();
    searchController = new AbortController();
    try {
        const response = await fetch("/search?q=" + encodeURIComponent(query), {
            signal: searchController.signal, cache: "no-store"
        });
        await requireSuccess(response, "Search failed");
        const data = await response.json();
        if (version !== searchVersion) return;
        renderSearchResults(data.results);
        searchStatus.textContent = data.has_more ? "Showing the first 100 matches. Refine your search."
            : data.results.length ? data.results.length + " matching chats" : "No chats found. Try different words.";
    } catch (error) {
        if (version === searchVersion && error.name !== "AbortError") searchStatus.textContent = error.message;
    }
}

searchInput.addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchController?.abort();
    ++searchVersion;
    if (!searchInput.value.trim()) { runSearch(); return; }
    searchPanel.hidden = false;
    conversationList.hidden = true;
    clearSearchButton.hidden = false;
    searchResults.replaceChildren();
    searchStatus.textContent = "Searching…";
    searchTimer = setTimeout(runSearch, 300);
});
clearSearchButton.addEventListener("click", () => { searchInput.value = ""; runSearch(); searchInput.focus(); });
searchInput.addEventListener("keydown", event => {
    if (event.key === "Escape") { event.stopPropagation(); clearSearchButton.click(); }
});

async function getConversationMessages(conversationId) {
    const response = await fetch("/conversations/" + conversationId + "/messages", { cache: "no-store" });
    await requireSuccess(response, "Couldn't load the saved conversation");
    const data = await response.json();
    if (!Array.isArray(data.messages) || !data.messages.every(message =>
        message && ["user", "assistant"].includes(message.role) &&
        typeof message.content === "string"
    )) {
        throw new Error("The server returned an invalid conversation.");
    }
    return data.messages;
}

function redrawMessages(messages) {
    clearConversationView();
    followOutput = false;
    for (const message of messages) {
        if (message.role === "user") {
            addMessage("You", message.content);
        } else {
            const content = addMessage("AI", "");
            // Stored Markdown is untrusted too: sanitize it again on restore.
            renderAssistantMessage(content, message.content);
        }
    }
    conversationTitle.textContent = savedConversations.find(item => item.id === currentConversationId)?.title
        || "New conversation";
    scrollToBottom(true);
    showStorageNotice("");
}

async function openConversation(conversationId) {
    if (isBusy) return;
    setBusy("loading");
    try {
        // Keep the current view and selection if loading another chat fails.
        const messages = await getConversationMessages(conversationId);
        currentConversationId = conversationId;
        redrawMessages(messages);
        renderConversationList();
        renderSearchResults();
        input.value = "";
        setSidebar(false);
    } catch (error) {
        showStorageNotice(error.message);
    } finally {
        setBusy();
        resizeInput();
        input.focus({ preventScroll: true });
    }
}

async function createConversation() {
    const response = await fetch("/conversations", { method: "POST" });
    await requireSuccess(response, "Couldn't create a conversation");
    const item = await response.json();
    if (!validConversation(item)) throw new Error("The server returned an invalid conversation.");
    currentConversationId = item.id;
    savedConversations.unshift(item);
    renderConversationList();
    return item;
}

async function showRequestError(error, conversationId) {
    // A failed request may already have saved its user message. Reconcile only
    // that chat with SQLite; never retry automatically or borrow another chat.
    try {
        if (conversationId !== null) {
            const messages = await getConversationMessages(conversationId);
            if (currentConversationId === conversationId) redrawMessages(messages);
        }
    } catch {
        showStorageNotice("The saved conversation couldn't be reloaded. Refresh to check what was saved.");
    }
    if (currentConversationId === conversationId) addMessage("Error", error.message);
}

// Enter sends; Shift + Enter and input-method composition keep typing.
document.getElementById("messageForm").addEventListener("submit", (event) => {
    event.preventDefault();
    sendMessage();
});
input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing && event.keyCode !== 229) {
        event.preventDefault();
        document.getElementById("messageForm").requestSubmit();
    }
});
input.addEventListener("input", resizeInput);

function resizeInput() {
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight, 180) + "px";
    button.disabled = isBusy || input.value.trim() === "";
}

// Follow new text unless the user scrolls up to read earlier messages.
conversation.addEventListener("scroll", () => {
    followOutput = conversation.scrollHeight - conversation.scrollTop - conversation.clientHeight < 80;
});
function scrollToBottom(force = false) {
    if (force || followOutput) {
        conversation.scrollTop = conversation.scrollHeight;
        followOutput = true;
    }
}

function setBusy(operation = "") {
    isBusy = Boolean(operation);
    isSending = operation === "sending";
    input.disabled = isBusy;
    newChatButton.disabled = isBusy;
    conversationList.querySelectorAll("button").forEach(item => { item.disabled = isBusy; });
    searchResults.querySelectorAll("button").forEach(item => { item.disabled = isBusy; });
    searchInput.disabled = isBusy;
    clearSearchButton.disabled = isBusy;
    button.disabled = isBusy || input.value.trim() === "";
    sendLabel.textContent = isSending ? "Generating…" : "Send";
    statusText.textContent = isSending ? "Replying…"
        : operation === "loading" ? "Loading conversation…"
        : "Ready to chat";
    status.dataset.generating = String(isSending);
    chat.setAttribute("aria-busy", String(isBusy));
}

// User messages, errors, and placeholders always start as literal text.
function addMessage(role, text) {
    welcome.hidden = true;
    chat.hidden = false;
    const article = document.createElement("article");
    const label = document.createElement("span");
    const content = document.createElement("div");
    const kind = role === "You" ? "user" : role === "AI" ? "assistant" : "error";

    article.className = "message message-" + kind;
    label.className = "message-label";
    label.textContent = role === "AI" ? "My AI Assistant" : role;
    content.className = "message-content";
    content.textContent = text;
    if (kind === "error") article.setAttribute("role", "alert");

    article.append(label, content);
    chat.appendChild(article);
    scrollToBottom();
    return content;
}

function renderAssistantMessage(content, markdown) {
    try {
        if (!window.marked?.parse || !window.DOMPurify?.isSupported) {
            throw new Error("Markdown libraries are unavailable.");
        }

        // Reparse the whole accumulated reply: Markdown can span many chunks.
        const html = marked.parse(markdown, { gfm: true, breaks: false, async: false });
        const safeContent = DOMPurify.sanitize(html, {
            ALLOWED_TAGS: [
                "p", "br", "h1", "h2", "h3", "h4", "h5", "h6",
                "strong", "em", "del", "ul", "ol", "li", "blockquote",
                "pre", "code", "hr", "table", "thead", "tbody", "tr",
                "th", "td", "a"
            ],
            ALLOWED_ATTR: ["href", "title", "start", "align"],
            ALLOW_DATA_ATTR: false,
            ALLOW_ARIA_ATTR: false,
            RETURN_DOM_FRAGMENT: true
        });

        // Only sanitized nodes enter the page; never insert raw model HTML.
        content.replaceChildren(safeContent);
        content.classList.add("markdown-content");
    } catch {
        // A loading or rendering problem must still leave a readable reply.
        content.classList.remove("markdown-content");
        content.textContent = markdown;
    }
}

async function sendMessage() {
    const message = input.value.trim();
    if (!message || isBusy) return;

    setBusy("sending");
    showStorageNotice("");
    if (!chat.querySelector(".message-user")) conversationTitle.textContent = message;
    addMessage("You", message);
    const assistantText = addMessage("AI", "Waiting for a reply…");
    assistantText.classList.add("waiting");
    input.value = "";
    resizeInput();
    scrollToBottom(true);

    let reader;
    let reply = "";
    let conversationId = currentConversationId;
    let postStarted = false;
    titlePolls = 0;
    try {
        // New chat is only a draft until its first message is sent.
        if (conversationId === null) {
            const created = await createConversation();
            conversationId = created.id;
        }
        postStarted = true;
        const response = await fetch("/chat", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ conversation_id: conversationId, message: message })
        });
        await requireSuccess(response, "Couldn't generate a reply");
        if (!response.body) throw new Error("Your browser did not provide a response stream.");

        reader = response.body.getReader();
        const decoder = new TextDecoder();
        while (true) {
            const { value, done } = await reader.read();
            if (done) break;
            reply += decoder.decode(value, { stream: true });
            if (reply) {
                assistantText.classList.remove("waiting");
                renderAssistantMessage(assistantText, reply);
                scrollToBottom();
            }
        }

        reply += decoder.decode();
        if (!reply.trim()) throw new Error("The model returned an empty reply.");
        renderAssistantMessage(assistantText, reply);
        // The backend saves this complete Markdown reply before closing the stream.
    } catch (error) {
        // The user message may already be saved. Do not duplicate it as a retry draft.
        assistantText.classList.remove("waiting");
        if (!reply) assistantText.textContent = "No reply received.";
        if (!postStarted) {
            clearConversationView();
            input.value = message;
        }
        await showRequestError(error, conversationId);
    } finally {
        if (reader) reader.releaseLock();
        assistantText.classList.remove("waiting");
        try {
            await refreshSidebar();
        } catch {
            showStorageNotice("The chat list couldn't be refreshed. Your saved chats will load when you refresh the page.");
        }
        setBusy();
        resizeInput();
        scrollToBottom();
        input.focus({ preventScroll: true });
    }
}

// New chat opens a draft. It neither deletes history nor creates an empty row.
newChatButton.addEventListener("click", () => {
    if (isBusy) return;
    currentConversationId = null;
    clearConversationView();
    renderConversationList();
    input.value = "";
    showStorageNotice("");
    followOutput = true;
    conversation.scrollTop = 0;
    setSidebar(false);
    resizeInput();
    input.focus({ preventScroll: true });
});
document.querySelectorAll(".suggestion").forEach((suggestion) => {
    suggestion.addEventListener("click", () => {
        if (isBusy) return;
        input.value = suggestion.dataset.prompt;
        resizeInput();
        input.focus();
    });
});

function setSidebar(open) {
    const drawerOpen = open && mobileLayout.matches;
    document.body.classList.toggle("sidebar-open", drawerOpen);
    menuButton.setAttribute("aria-expanded", String(drawerOpen));
    sidebar.inert = mobileLayout.matches && !drawerOpen;
    mainContent.inert = drawerOpen;
    if (drawerOpen) document.getElementById("closeSidebar").focus();
}
function closeSidebar() {
    setSidebar(false);
    if (mobileLayout.matches) menuButton.focus();
}
menuButton.addEventListener("click", () => setSidebar(true));
document.getElementById("closeSidebar").addEventListener("click", closeSidebar);
document.getElementById("sidebarBackdrop").addEventListener("click", closeSidebar);
document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && document.body.classList.contains("sidebar-open")) closeSidebar();
});
mobileLayout.addEventListener("change", () => setSidebar(false));
setSidebar(false);
resizeInput();

async function initializeApp() {
    setBusy("loading");
    try {
        const conversations = await refreshSidebar();
        if (conversations.length) {
            const selectedId = conversations[0].id;
            const messages = await getConversationMessages(selectedId);
            currentConversationId = selectedId;
            redrawMessages(messages);
        } else {
            currentConversationId = null;
            clearConversationView();
        }
        renderConversationList();
    } catch (error) {
        showStorageNotice(error.message + " Refresh to load your saved chat.");
    } finally {
        setBusy();
        resizeInput();
    }
}

initializeApp();
