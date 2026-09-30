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
    for (const item of conversations) {
        const itemButton = document.createElement("button");
        itemButton.type = "button";
        itemButton.className = "conversation-item";
        itemButton.dataset.conversationId = String(item.id);
        itemButton.textContent = item.title;
        itemButton.title = item.title;
        itemButton.disabled = isBusy;
        if (item.id === currentConversationId) itemButton.setAttribute("aria-current", "page");
        itemButton.addEventListener("click", () => openConversation(item.id));
        conversationList.appendChild(itemButton);
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
    return savedConversations;
}

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
    try {
        const response = await fetch("/chat", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ message: message })
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
        await showRequestError(error);
    } finally {
        if (reader) reader.releaseLock();
        assistantText.classList.remove("waiting");
        setBusy();
        resizeInput();
        scrollToBottom();
        input.focus({ preventScroll: true });
    }
}

// Lesson 9 has one conversation: New chat deletes its rows from SQLite.
newChatButton.addEventListener("click", async () => {
    if (isBusy) return;
    setBusy("clearing");
    try {
        const response = await fetch("/messages", { method: "DELETE" });
        await requireSuccess(response, "Couldn't clear the saved conversation");
        clearConversationView();
        input.value = "";
        showStorageNotice("");
        followOutput = true;
        conversation.scrollTop = 0;
        setSidebar(false);
    } catch (error) {
        await showRequestError(error);
    } finally {
        setBusy();
        resizeInput();
        input.focus({ preventScroll: true });
    }
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
document.getElementById("currentChatButton").addEventListener("click", () => {
    closeSidebar();
    scrollToBottom(true);
});
document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && document.body.classList.contains("sidebar-open")) closeSidebar();
});
mobileLayout.addEventListener("change", () => setSidebar(false));
setSidebar(false);
resizeInput();

async function initializeConversation() {
    setBusy("loading");
    try {
        await loadConversation();
    } catch (error) {
        showStorageNotice(error.message + " Refresh to load your saved chat.");
    } finally {
        setBusy();
        resizeInput();
    }
}

initializeConversation();
