/* Pure cursor-aware Markdown edits, plus a small native-textarea adapter. */
(function (root) {
    "use strict";
    const listPattern = /^([\t ]*)([-*+]|(\d{1,9})([.)]))([\t ]+)(.*)$/;

    function fenceBefore(text, lineStart) {
        let fence = null;
        for (const line of text.slice(0, lineStart).split("\n")) {
            const match = /^([ \t]*)(`{3,}|~{3,})(.*)$/.exec(line);
            if (!match) continue;
            if (!fence) {
                if (match[2][0] === "`" && match[3].includes("`")) continue;
                fence = { indent: match[1], marker: match[2] };
            } else if (match[2][0] === fence.marker[0] && match[2].length >= fence.marker.length && !match[3].trim()) {
                fence = null;
            }
        }
        return fence;
    }

    function editFor(text, start, end, key, { shift = false, mobile = false } = {}) {
        const lineStart = start === 0 ? 0 : text.lastIndexOf("\n", start - 1) + 1;
        const nextBreak = text.indexOf("\n", start);
        const lineEnd = nextBreak < 0 ? text.length : nextBreak;
        const line = text.slice(lineStart, lineEnd);
        const before = text.slice(lineStart, start);
        const indent = /^[\t ]*/.exec(before)[0];
        const fence = fenceBefore(text, lineStart);
        const list = listPattern.exec(before);
        const replace = (from, to, insert, selection) => ({ from, to, insert, selection });

        if (key === "Tab") {
            // Ordinary prose keeps normal Tab navigation. Lists, code, and
            // multi-line selections opt into indentation, including on touch.
            if (!fence && !listPattern.test(line) && !text.slice(start, end).includes("\n")) return null;
            const lastBreak = text.indexOf("\n", Math.max(start, end - 1));
            const blockEnd = lastBreak < 0 ? text.length : lastBreak;
            const block = text.slice(lineStart, blockEnd);
            const lines = block.split("\n");
            let totalChange = 0;
            let firstChange = 0;
            const edited = lines.map((value, index) => {
                const removed = shift ? (/^(?:\t| {1,2})/.exec(value)?.[0].length || 0) : 0;
                const delta = shift ? -removed : 2;
                if (index === 0) firstChange = delta;
                totalChange += delta;
                return shift ? value.slice(removed) : "  " + value;
            }).join("\n");
            const mappedStart = Math.max(lineStart, start + firstChange);
            const mappedEnd = start === end ? mappedStart : Math.max(mappedStart, end + totalChange);
            return replace(lineStart, blockEnd, edited, [mappedStart, mappedEnd]);
        }
        if (key === "Backspace" && start === end && start === lineEnd && list && !list[6].trim() && !fence) {
            return replace(lineStart, end, list[1]);
        }
        if (key !== "Enter") return null;
        if (shift) return replace(start, end, "\n");
        const marker = /^([ \t]*)(`{3,}|~{3,})(.*)$/.exec(line);
        if (fence) {
            if (marker && marker[2][0] === fence.marker[0] && marker[2].length >= fence.marker.length && !marker[3].trim()) {
                return replace(start, end, "\n" + fence.indent);
            }
            // Enter on an empty code line closes the fence. Shift+Enter is
            // always available for a literal blank line within the block.
            if (start === end && start === lineEnd && !line.trim()) {
                return replace(lineStart, end, fence.indent + fence.marker + "\n");
            }
            return replace(start, end, "\n" + indent);
        }
        if (marker) return replace(start, end, "\n" + marker[1]);
        if (list) {
            if (!list[6].trim() && start === end && start === lineEnd) return replace(lineStart, end, list[1]);
            const marker = list[3] ? String(Number(list[3]) + 1) + list[4] : list[2];
            return replace(start, end, "\n" + list[1] + marker + " ");
        }
        if (listPattern.test(line)) return replace(start, end, "\n" + indent);
        return mobile ? replace(start, end, "\n") : { send: true };
    }

    function attach(textarea, { send, changed, busy }) {
        let composing = false;
        let applying = false;
        let compositionEnded = -Infinity;
        const inComposition = event => composing || event.isComposing || event.keyCode === 229 || performance.now() - compositionEnded < 60;
        textarea.addEventListener("compositionstart", () => { composing = true; });
        textarea.addEventListener("compositionend", () => { composing = false; compositionEnded = performance.now(); changed(); });
        function apply(edit) {
            if (!edit) return false;
            if (edit.send) { send(); return true; }
            const selection = [textarea.selectionStart, textarea.selectionEnd, textarea.selectionDirection];
            textarea.focus({ preventScroll: true });
            textarea.setSelectionRange(edit.from, edit.to);
            applying = true;
            let applied = false;
            try {
                // insertText keeps browser undo/redo history, unlike assigning
                // .value or setRangeText. Plain text only; never insertHTML.
                applied = document.execCommand("insertText", false, edit.insert);
            } catch {
                applied = false;
            } finally { applying = false; }
            if (!applied) {
                textarea.setSelectionRange(...selection);
                return false; // Let native typing work if editing commands are unavailable.
            }
            if (edit.selection) textarea.setSelectionRange(...edit.selection, selection[2]);
            changed();
            return true;
        }
        textarea.addEventListener("keydown", event => {
            if (busy() || inComposition(event) || event.altKey || event.metaKey || event.ctrlKey) return;
            if (apply(editFor(textarea.value, textarea.selectionStart, textarea.selectionEnd, event.key, { shift: event.shiftKey }))) event.preventDefault();
        });
        textarea.addEventListener("beforeinput", event => {
            if (applying || busy() || inComposition(event) || !event.cancelable) return;
            // Touch keyboards may emit beforeinput without a keydown. Their
            // return key inserts text; the visible Send button submits.
            const key = event.inputType === "insertLineBreak" ? "Enter" : event.inputType === "deleteContentBackward" ? "Backspace" : "";
            if (key && apply(editFor(textarea.value, textarea.selectionStart, textarea.selectionEnd, key, { mobile: true }))) event.preventDefault();
        });
        return { indent: shift => apply(editFor(textarea.value, textarea.selectionStart, textarea.selectionEnd, "Tab", { shift })),
            isComposing: () => composing };
    }

    // Reserved extension point: slash text remains ordinary text until a
    // command registry is deliberately introduced in a later lesson.
    const commands = new Map();
    const api = { editFor, fenceBefore, attach, commands };
    if (typeof module !== "undefined") module.exports = api;
    else root.SmartComposer = api;
})(typeof window === "undefined" ? globalThis : window);
