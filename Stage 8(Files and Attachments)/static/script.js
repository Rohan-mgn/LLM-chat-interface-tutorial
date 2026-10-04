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
let sidebarVersion = 0;
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

const stopButton = document.getElementById("stopButton");
const branchNotice = document.getElementById("branchNotice");
const openParent = document.getElementById("openParent");
function updateBranchNotice() {
    const parent = savedConversations.find(item => item.id === currentConversationId)?.parent_conversation_id;
    branchNotice.hidden = !parent;
    openParent.disabled = isBusy;
}
openParent.addEventListener("click", () => {
    const parent = savedConversations.find(item => item.id === currentConversationId)?.parent_conversation_id;
    if (parent && !isBusy) openConversation(parent);
});
let currentMessages = [];
let editContext = null;
let activeGeneration = null;
let attachmentComposer = null;
const conversationTrees = new Map();
let viewVersion = 0;
let actionVersion = 0;
// Choices are persisted in SQLite for each parent, including hidden branches.
function getChildren(parentId, tree = conversationTrees.get(currentConversationId)) {
    return (tree?.messages || []).filter(message => message.parent_id === parentId);
}
function getSiblings(message) { return getChildren(message.parent_id); }
function getActiveConversationPath(tree) {
    const path = [];
    let children = getChildren(null, tree);
    const seen = new Set();
    while (children.length) {
        const parent = children[0].parent_id ?? 0;
        const selected = children.find(message => message.id === tree.active_children[String(parent)]) || children[0];
        if (seen.has(selected.id)) throw new Error("Invalid conversation tree.");
        seen.add(selected.id);
        path.push(selected);
        children = getChildren(selected.id, tree);
    }
    return path;
}
function actionAllowed(control) {
    return control.dataset.boundary !== "true" && (!isBusy || control.dataset.availability === "always" ||
        (isSending && control.dataset.availability === "generation"));
}
const draftCache = new Map();
const draftKey = id => "stage8:draft:" + (id ?? "new");
function readDraft(id) {
    if (draftCache.has(draftKey(id))) return draftCache.get(draftKey(id));
    try {
        const draft = JSON.parse(sessionStorage.getItem(draftKey(id)) || "null");
        return draft && typeof draft.text === "string" ? draft : null;
    } catch { return null; }
}
function writeDraft(id, draft) {
    draftCache.set(draftKey(id), draft);
    try {
        if (draft) sessionStorage.setItem(draftKey(id), JSON.stringify(draft));
        else sessionStorage.removeItem(draftKey(id));
    } catch { /* Keep drafts in memory when browser storage is unavailable. */ }
}
function saveDraft() {
    writeDraft(currentConversationId, { text: input.value, start: input.selectionStart,
        end: input.selectionEnd, direction: input.selectionDirection });
}
function restoreDraft() {
    const draft = readDraft(currentConversationId);
    input.value = draft?.text || "";

    input.setSelectionRange(draft?.start ?? input.value.length, draft?.end ?? input.value.length, draft?.direction || "none");
    resizeInput();
    return attachmentComposer?.restore();
}
// A later click supersedes an earlier pending edit/regenerate/switch. All
// transitions wait for the old generation to close and reconcile its saved row.
async function afterCancellation(action) {
    if (isBusy && !isSending) return;
    const version = ++actionVersion;
    try {
        await cancelGeneration();
        if (version !== actionVersion) return;
        await action();
    } catch (error) { showStorageNotice(error.message); }
}
function beginEdit(message) {
    return afterCancellation(() => {
        cancelEdit(false);
        const article = chat.querySelector(`[data-message-id="${message.id}"]`);
        if (!article) return;
        editContext = { messageId: message.id };
        const form = document.createElement("form");
        form.className = "message-edit";
        const textarea = document.createElement("textarea");
        textarea.value = message.content;
        textarea.rows = 4;
        textarea.setAttribute("aria-label", "Edit user message");
        const actions = document.createElement("div");
        actions.className = "edit-actions";
        const submit = document.createElement("button");
        submit.type = "submit";
        submit.textContent = "Save and generate";
        const cancel = document.createElement("button");
        cancel.type = "button";
        cancel.textContent = "Cancel";
        cancel.addEventListener("click", () => cancelEdit());
        actions.append(submit, cancel);
        form.append(textarea, actions);
        article.querySelector(".message-content").hidden = true;
        article.querySelector(".message-actions").hidden = true;
        article.append(form);
        form.addEventListener("submit", event => {
            event.preventDefault();
            if (isBusy || !textarea.value.trim()) return;
            if (textarea.value === message.content) { cancelEdit(); return; }
            const text = textarea.value;
            cancelEdit(false);
            generateResponse("edit", message.id, text);
        });
        textarea.addEventListener("keydown", event => {
            if (event.key === "Escape" && !event.isComposing) { event.preventDefault(); cancelEdit(); }
        });
        setBusy();
        textarea.focus();
        textarea.setSelectionRange(textarea.value.length, textarea.value.length);
    });
}
function cancelEdit(focus = true) {
    if (!editContext) return;
    const id = editContext.messageId;
    editContext = null;
    redrawMessages(currentMessages);
    setBusy();
    if (focus) chat.querySelector(`[data-message-id="${id}"] button[data-action="edit"]`)?.focus();
}
async function switchBranch(messageId) {
    return afterCancellation(async () => {
        setBusy("loading");
        try {
            const tree = await fetchJson(`/conversations/${currentConversationId}/branch`, "Couldn't switch branch", {
                method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ message_id: messageId })
            });
            ++viewVersion;
            editContext = null;
            conversationTrees.set(currentConversationId, tree);
            redrawMessages(getActiveConversationPath(tree));
            chat.querySelector(`[data-message-id="${messageId}"] .branch-position`)?.focus({ preventScroll: true });
        } finally { setBusy(); }
    });
}
window.addEventListener("pagehide", saveDraft);

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

async function fetchJson(url, action, options = {}, allowMissing = false) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 10000);
    try {
        const response = await fetch(url, { cache: "no-store", ...options, signal: controller.signal });
        if (allowMissing && response.status === 404) return null;
        await requireSuccess(response, action);
        // Keep the timeout active until the body is read, not just the headers.
        return await response.json();
    } catch (error) {
        if (error.name === "AbortError") {
            throw new Error(action + ": the server took too long to respond. Check that Stage 8 is running, then refresh.");
        }
        throw error;
    } finally {
        clearTimeout(timeout);
    }
}

function clearConversationView() {
    editContext = null;
    chat.replaceChildren();
    chat.hidden = true;
    welcome.hidden = false;
    conversationTitle.textContent = "New conversation";
    branchNotice.hidden = true;
}

function validConversation(item) {
    return item && Number.isSafeInteger(item.id) && item.id > 0 &&
        typeof item.title === "string";
}

async function getConversations() {
    const data = await fetchJson("/conversations", "Couldn't load saved chats");
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
    const version = ++sidebarVersion;
    const conversations = await getConversations();
    // A slower poll must never undo a newer refresh, rename, or deletion.
    if (version !== sidebarVersion) return savedConversations;
    savedConversations = conversations;
    renderConversationList();
    const selected = savedConversations.find(item => item.id === currentConversationId);
    if (selected) conversationTitle.textContent = selected.title;
    updateBranchNotice();
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
        ++sidebarVersion;
        const deletedActive = deleting && currentConversationId === item.id;
        if (deleting) { savedConversations = savedConversations.filter(saved => saved.id !== item.id); writeDraft(item.id, null); attachmentComposer?.forget(item.id); }
        else savedConversations = savedConversations.map(saved => saved.id === item.id ? { ...saved, title, title_source: "manual" } : saved);
        if (deletedActive) { currentConversationId = null; currentMessages = []; editContext = null; clearConversationView(); restoreDraft(); }
        renderConversationList();
        // The mutation succeeded even if a subsequent refresh fails.
        actionDialog.close();
        await refreshSidebar();
        if (deletedActive && savedConversations.length) {
            const nextId = savedConversations[0].id;
            const messages = await getConversationMessages(nextId);
            currentConversationId = nextId;
            redrawMessages(messages);
            restoreDraft();
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

function highlightText(element, value, ranges = []) {
    // Build literal text and <mark> nodes; never interpret search content as HTML.
    const characters = Array.from(value || "");
    let position = 0;
    for (const [start, end] of ranges) {
        element.append(document.createTextNode(characters.slice(position, start).join("")));
        const mark = document.createElement("mark");
        mark.textContent = characters.slice(start, end).join("");
        element.append(mark);
        position = end;
    }
    element.append(document.createTextNode(characters.slice(position).join("")));
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
        highlightText(title, item.title, item.title_matches);
        const source = document.createElement("span");
        source.className = "search-source";
        source.textContent = item.message_id ? (item.role === "user" ? "You" : "Assistant") + " · message #" + item.message_id : "Title match";
        const snippet = document.createElement("span");
        highlightText(snippet, item.snippet, item.snippet_matches);
        hit.append(title, source, snippet);
        hit.addEventListener("click", () => openConversation(item.conversation_id, item.message_id));
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
    lastSearchResults = [];
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
    lastSearchResults = [];
    searchTimer = setTimeout(runSearch, 300);
});
clearSearchButton.addEventListener("click", () => { searchInput.value = ""; runSearch(); searchInput.focus(); });
searchInput.addEventListener("keydown", event => {
    if (event.key === "Escape") { event.stopPropagation(); clearSearchButton.click(); }
});

async function getConversationMessages(conversationId, selectId = null) {
    const selecting = Number.isSafeInteger(selectId);
    const data = await fetchJson(`/conversations/${conversationId}/${selecting ? "branch" : "tree"}`,
        "Couldn't load the saved conversation", selecting ? {
            method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ message_id: selectId })
        } : {});
    if (!Array.isArray(data.messages) || !data.active_children || !data.messages.every(message =>
        message && ["user", "assistant"].includes(message.role) && typeof message.content === "string")) {
        throw new Error("The server returned an invalid conversation tree.");
    }
    conversationTrees.set(conversationId, data);
    return getActiveConversationPath(data);
}

function redrawMessages(messages) {
    currentMessages = messages;
    clearConversationView();
    followOutput = false;
    for (const message of messages) {
        let content;
        if (message.role === "user") {
            content = addMessage("You", message.content);
        } else {
            content = addMessage("AI", "");
            // Stored Markdown is untrusted too: sanitize it again on restore.
            renderAssistantMessage(content, message.content);
        }
        if (Number.isSafeInteger(message.id)) {
            const article = content.closest("article");
            article.dataset.messageId = String(message.id);
            AttachmentUI.renderHistory(article, message.attachments);
            attachMessageActions(article, message);
        }
    }
    conversationTitle.textContent = savedConversations.find(item => item.id === currentConversationId)?.title
        || "New conversation";
    scrollToBottom(true);
    showStorageNotice("");
}

async function openConversation(conversationId, messageId = null) {
    if (isBusy) return;
    let matchingMessage;
    saveDraft();
    setBusy("loading");
    try {
        // Keep the current view and selection if loading another chat fails.
        const messages = await getConversationMessages(conversationId, messageId);
        ++viewVersion;
        currentConversationId = conversationId;
        redrawMessages(messages);
        renderConversationList();
        renderSearchResults();
        await restoreDraft();
        setSidebar(false);
        if (Number.isSafeInteger(messageId)) {
            matchingMessage = chat.querySelector('[data-message-id="' + messageId + '"]');
            if (matchingMessage) {
                matchingMessage.classList.add("search-target");
                matchingMessage.tabIndex = -1;
                matchingMessage.setAttribute("aria-label", "Matching message");
                matchingMessage.scrollIntoView({ block: "center" });
                matchingMessage.focus({ preventScroll: true });
                followOutput = false;
            }
        }
    } catch (error) {
        showStorageNotice(error.message);
    } finally {
        setBusy();
        resizeInput();
        if (!matchingMessage) input.focus({ preventScroll: true });
    }
}

async function createConversation() {
    const response = await fetch("/conversations", { method: "POST" });
    await requireSuccess(response, "Couldn't create a conversation");
    const item = await response.json();
    if (!validConversation(item)) throw new Error("The server returned an invalid conversation.");
    currentConversationId = item.id;
    ++sidebarVersion;
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

document.getElementById("messageForm").addEventListener("submit", event => {
    event.preventDefault();
    if (!smartComposer.isComposing()) sendMessage();
});
const smartComposer = SmartComposer.attach(input, {
    send: () => document.getElementById("messageForm").requestSubmit(),
    changed: () => { resizeInput(); saveDraft(); },
    busy: () => input.disabled
});
input.addEventListener("input", () => { resizeInput(); saveDraft(); });
input.addEventListener("select", () => { if (!isBusy) saveDraft(); });
for (const [id, shift] of [["indentButton", false], ["unindentButton", true]]) {
    document.getElementById(id).addEventListener("click", () => {
        if (!isBusy) smartComposer.indent(shift);
    });
}

function resizeInput() {
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight, 180) + "px";
    input.style.overflowY = input.scrollHeight > 180 ? "auto" : "hidden";
    button.disabled = isBusy || Boolean(editContext) || Boolean(attachmentComposer?.blocked()) || (!input.value.trim() && !attachmentComposer?.hasFiles());
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
    updateBranchNotice();
    // Loading history must not prevent the user from drafting a new message.
    // Sending and switching chats still wait for initialization to settle.
    input.disabled = Boolean(editContext) || (isBusy && operation !== "initializing");
    newChatButton.disabled = isBusy;
    chat.querySelectorAll(".message-actions button").forEach(item => {
        item.disabled = !actionAllowed(item);
    });
    document.querySelectorAll(".composer-tools button").forEach(item => { item.disabled = isBusy || Boolean(editContext); });
    stopButton.hidden = !isSending;
    stopButton.disabled = !activeGeneration || activeGeneration.stopRequested;
    button.hidden = isSending;
    conversationList.querySelectorAll("button").forEach(item => { item.disabled = isBusy; });
    searchResults.querySelectorAll("button").forEach(item => { item.disabled = isBusy; });
    searchInput.disabled = isBusy;
    clearSearchButton.disabled = isBusy;
    button.disabled = isBusy || Boolean(editContext) || Boolean(attachmentComposer?.blocked()) || (!input.value.trim() && !attachmentComposer?.hasFiles());
    sendLabel.textContent = "Send";
    statusText.textContent = isSending ? "Replying…"
        : ["loading", "initializing"].includes(operation) ? "Loading conversation…"
        : "Ready to chat";
    status.dataset.generating = String(isSending);
    chat.setAttribute("aria-busy", String(isBusy));
    attachmentComposer?.render();
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

async function copyMessage(message, control) {
    try {
        await navigator.clipboard.writeText(message.content);
        control.textContent = "Copied";
        control.setAttribute("aria-label", "Copied message");
        setTimeout(() => { control.textContent = "Copy"; control.setAttribute("aria-label", "Copy message"); }, 1800);
    } catch {
        showStorageNotice("Clipboard access was unavailable. Select the message text to copy it.");
    }
}

function attachMessageActions(article, message) {
    const actions = document.createElement("div");
    actions.className = "message-actions";
    actions.setAttribute("aria-label", "Message actions");
    const add = (text, label, handler, availability = "idle") => {
        const control = document.createElement("button");
        control.type = "button";
        control.textContent = text;
        control.title = label;
        control.setAttribute("aria-label", label);
        control.dataset.availability = availability;
        control.disabled = !actionAllowed(control);
        control.addEventListener("click", () => { if (actionAllowed(control)) handler(control); });
        actions.append(control);
        return control;
    };
    // Copy never mutates history; streamed assistant text is kept up to date.
    add("Copy", "Copy message", control => copyMessage(message, control), "always");
    if (message.role === "user") {
        add("Edit", "Edit this message in a new branch", () => beginEdit(message), "generation").dataset.action = "edit";
        const last = currentMessages.at(-1);
        if (last?.id === message.id) add("Retry", "Retry the reply to this user message", () => afterCancellation(() => generateResponse("retry", message.id)), "generation");
    } else {
        const incomplete = message.status !== "completed";
        const badge = document.createElement("span");
        badge.className = "message-state";
        badge.textContent = { generating: "Generating…", stopped: "Stopped · incomplete", error: "Failed · incomplete" }[message.status] || "";
        if (incomplete) article.append(badge);
        const retryable = message.status === "error";
        add(retryable ? "Retry" : "Regenerate", retryable ? "Retry this incomplete response" : "Regenerate this response in a new branch",
            () => afterCancellation(() => generateResponse(retryable ? "retry" : "regenerate", message.id)), "generation");
        if (!incomplete) {
            for (const [value, text, label] of [[1, "👍", "Helpful response"], [-1, "👎", "Unhelpful response"]]) {
                const control = add(text, label, async () => {
                    const next = message.feedback === value ? null : value;
                    control.disabled = true;
                    try {
                        const response = await fetch(`/conversations/${currentConversationId}/messages/${message.id}/feedback`, {
                            method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ value: next })
                        });
                        await requireSuccess(response, "Couldn't save feedback");
                        message.feedback = next;
                        actions.querySelectorAll("[data-feedback]").forEach(button => button.setAttribute("aria-pressed", String(Number(button.dataset.feedback) === next)));
                    } catch (error) { showStorageNotice(error.message); }
                    finally { control.disabled = isBusy; }
                });
                control.dataset.feedback = String(value);
                control.setAttribute("aria-pressed", String(message.feedback === value));
            }
        }
    }
    const siblings = getSiblings(message);
    if (siblings.length > 1) {
        const index = siblings.findIndex(sibling => sibling.id === message.id);
        const previous = add("\u2039", "Previous version", () => switchBranch(siblings[index - 1].id), "generation");
        previous.dataset.boundary = String(index === 0);
        previous.disabled = !actionAllowed(previous);
        const position = document.createElement("span");
        position.className = "branch-position";
        position.textContent = `${index + 1} / ${siblings.length}`;
        position.tabIndex = -1;
        position.setAttribute("aria-label", `Version ${index + 1} of ${siblings.length}`);
        actions.append(position);
        const next = add("\u203a", "Next version", () => switchBranch(siblings[index + 1].id), "generation");
        next.dataset.boundary = String(index === siblings.length - 1);
        next.disabled = !actionAllowed(next);
    }
    article.append(actions);
}

async function sendMessage() {
    if ((!input.value.trim() && !attachmentComposer?.hasFiles()) || attachmentComposer?.blocked() || isBusy || editContext) return;
    return generateResponse("send", null, input.value);
}

async function generateResponse(action, messageId = null, text = "") {
    if (isBusy) return;
    editContext = null;
    saveDraft();
    const sourceId = currentConversationId;
    const sourceDraft = readDraft(sourceId);
    const attachmentIds = action === "send" ? attachmentComposer.ids() : [];
    const run = { id: crypto.randomUUID(), controller: new AbortController(), stopRequested: false, view: viewVersion };
    run.finished = new Promise(resolve => { run.resolveFinished = resolve; });
    activeGeneration = run;
    const ownsView = () => activeGeneration === run && viewVersion === run.view;
    const acceptsTokens = () => ownsView() && !run.stopRequested;
    setBusy("sending");
    showStorageNotice("");
    titlePolls = 0;
    let reader, reply = "", assistantText, assistantMessage, accepted = false, finalEvent = false;
    const accept = async metadata => {
        if (accepted || !ownsView() || metadata.generation_id !== run.id) return;
        accepted = true;
        if (action === "send") { writeDraft(sourceId, null); attachmentComposer.sent(metadata.conversation_id); }
        currentConversationId = metadata.conversation_id;
        if (action === "send" && sourceId === null) writeDraft(currentConversationId, null);
        restoreDraft();
        const messages = await getConversationMessages(currentConversationId);
        if (!ownsView()) return;
        redrawMessages(messages);
        assistantMessage = messages.find(message => message.id === metadata.assistant_message_id);
        assistantText = chat.querySelector(`[data-message-id="${metadata.assistant_message_id}"] .message-content`);
        if (assistantText && !run.stopRequested) assistantText.classList.add("waiting");
        await refreshSidebar();
        if (ownsView()) scrollToBottom(true);
    };
    try {
        if (currentConversationId === null) {
            await createConversation();
            writeDraft(currentConversationId, sourceDraft);
        }
        if (run.stopRequested) throw new DOMException("Stopped", "AbortError");
        const response = await fetch("/chat", {
            method: "POST", signal: run.controller.signal,
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ conversation_id: currentConversationId, generation_id: run.id, action,
                parent_id: currentMessages.at(-1)?.id ?? null, message_id: messageId, message: text, attachment_ids: attachmentIds })
        });
        await requireSuccess(response, "Couldn't start the reply");
        if (!response.body) throw new Error("Streaming is unavailable in this browser.");
        reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        while (acceptsTokens()) {
            const { value, done } = await reader.read();
            buffer += done ? decoder.decode() : decoder.decode(value, { stream: true });
            let boundary;
            while ((boundary = buffer.indexOf("\n\n")) >= 0) {
                const packet = buffer.slice(0, boundary);
                buffer = buffer.slice(boundary + 2);
                if (!packet.startsWith("data: ")) continue;
                const event = JSON.parse(packet.slice(6));
                // Check every chunk, including chunks already buffered before
                // AbortController fired. No cancelled UUID can write to a new view.
                if (!acceptsTokens() || event.generation_id !== run.id) continue;
                if (event.type === "start") await accept(event);
                if (!acceptsTokens()) continue;
                if (event.type === "delta") {
                    reply += event.content;
                    if (assistantMessage) assistantMessage.content = reply;
                    if (assistantText) {
                        assistantText.classList.remove("waiting");
                        renderAssistantMessage(assistantText, reply);
                        scrollToBottom();
                    }
                }
                if (event.type === "error") showStorageNotice(event.message);
                if (event.type === "done") finalEvent = true;
            }
            if (done) break;
        }
        if (!finalEvent && !run.stopRequested) throw new Error("The connection ended early. Check the saved response before retrying.");
    } catch (error) {
        if (ownsView() && error.name !== "AbortError" && !run.stopRequested) showStorageNotice(error.message);
    } finally {
        if (reader) reader.releaseLock();
        try {
            if (run.stopPromise) await run.stopPromise;
            const metadata = await fetchJson("/generations/" + run.id, "Couldn't check the saved response", {}, true);
            if (metadata) await accept(metadata);
            // A lost connection can beat server cleanup. Explicitly settle it
            // before enabling another action, even if Stop wasn't clicked.
            if (metadata?.status === "generating") {
                await fetchJson(`/generations/${run.id}/stop`, "Couldn't confirm cancellation", { method: "POST" });
            }
            if (ownsView() && currentConversationId !== null) {
                const messages = await getConversationMessages(currentConversationId);
                if (ownsView()) {
                    const notice = storageNotice.textContent;
                    redrawMessages(messages);
                    if (notice) showStorageNotice(notice);
                }
            }
            if (ownsView()) {
                await refreshSidebar();
                if (searchInput.value.trim()) await runSearch();
            }
        } catch (error) {
            run.cleanupError = error;
            if (ownsView()) showStorageNotice(error.message + " Refresh to load the saved response.");
        } finally {
            if (ownsView()) {
                activeGeneration = null;
                setBusy();
                restoreDraft();
                scrollToBottom();
                input.focus({ preventScroll: true });
            }
            run.resolveFinished();
        }
    }
}

async function cancelGeneration() {
    const run = activeGeneration;
    if (!run) return;
    if (!run.stopRequested) {
        run.stopRequested = true;
        stopButton.disabled = true;
        statusText.textContent = "Stopping...";
        run.controller.abort();
        // The independent request waits for Ollama connection cleanup and the
        // stopped row to commit. It also cancels a not-yet-accepted request UUID.
        run.stopPromise = fetchJson(`/generations/${run.id}/stop`, "Couldn't confirm cancellation", { method: "POST" });
        // Install a handler now; generateResponse's finally awaits this promise.
        run.stopPromise.catch(() => {});
    }
    await run.finished;
    if (run.stopPromise) await run.stopPromise;
    if (run.cleanupError) throw run.cleanupError;
}
stopButton.addEventListener("click", () => {
    ++actionVersion;
    cancelGeneration().catch(error => showStorageNotice(error.message));
});

// New chat opens a draft. It neither deletes history nor creates an empty row.
newChatButton.addEventListener("click", () => {
    if (isBusy) return;
    saveDraft();
    currentConversationId = null;
    currentMessages = [];
    ++viewVersion;
    clearConversationView();
    renderConversationList();
    renderSearchResults();
    restoreDraft();
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
        saveDraft();
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
attachmentComposer = AttachmentUI.attach({
    conversationId: () => currentConversationId,
    busy: () => isBusy,
    editing: () => Boolean(editContext),
    changed: resizeInput,
    json: fetchJson,
    lock: locked => setBusy(locked ? "attachments" : ""),
    ensureConversation: async () => {
        if (currentConversationId === null) {
            saveDraft();
            const draft = readDraft(null);
            await createConversation();
            writeDraft(currentConversationId, draft);
            writeDraft(null, null);
        }
        return currentConversationId;
    }
});
resizeInput();

async function initializeApp() {
    restoreDraft();
    setBusy("initializing");
    try {
        const conversations = await refreshSidebar();
        if (conversations.length && !input.value) {
            const selectedId = conversations[0].id;
            const messages = await getConversationMessages(selectedId);
            // The user may have begun typing while either request was pending.
            // Keep that New Chat draft instead of switching it to saved history.
            if (!input.value) {
                currentConversationId = selectedId;
                redrawMessages(messages);
                await restoreDraft();
            }
        } else {
            currentConversationId = null;
            clearConversationView();
        }
        renderConversationList();
    } catch (error) {
        showStorageNotice(error.message + " Refresh to load your saved chat.");
    } finally {
        setBusy();
    }
}

initializeApp();
