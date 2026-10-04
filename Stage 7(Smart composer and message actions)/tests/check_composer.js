const assert = require("node:assert/strict");
const { editFor, fenceBefore, commands } = require("../static/composer.js");
let checks = 0;
function edit(text, key, expected, options = {}, start = text.length, end = start) {
    const result = editFor(text, start, end, key, options);
    const actual = !result ? null : result.send ? "SEND" : text.slice(0, result.from) + result.insert + text.slice(result.to);
    assert.equal(actual, expected, JSON.stringify({ text, key, start, end }));
    checks++;
    return result;
}
edit("Hello", "Enter", "SEND");
edit("Hello", "Enter", "Hello\n", { shift: true });
edit("Hello", "Enter", "Hello\n", { mobile: true });
for (const marker of ["-", "*", "+"]) {
    edit(marker + " First", "Enter", marker + " First\n" + marker + " ");
    edit("  " + marker + " ", "Backspace", "  ");
}
edit("9. First", "Enter", "9. First\n10. ");
edit("  29) Nested", "Enter", "  29) Nested\n  30) ");
edit("10. ", "Backspace", "");
edit("- ", "Enter", "");
edit("- hello world", "Enter", "- hello\n-  world", {}, 7);
edit("- hello world", "Enter", "- hello\n- ", {}, 7, 13);
edit("- First", "Enter", "- First\n", { shift: true });
edit("```python", "Enter", "```python\n");
edit("```python", "Enter", "```\npython", {}, 3);
edit("- First", "Enter", "\n- First", {}, 0);
edit("```py\n    x = 1", "Enter", "```py\n    x = 1\n    ");
edit("```py\n- literal", "Enter", "```py\n- literal\n");
edit("```py\n", "Enter", "```py\n```\n");
edit("```py\n", "Enter", "```py\n\n", { shift: true });
edit("```py\nx\n```", "Enter", "```py\nx\n```\n");
edit("```py\nx\n```\nHi", "Enter", "SEND");
edit("~~~js\n  x", "Enter", "~~~js\n  x\n  ");
edit("```\nx\n```\n", "Tab", null);
edit("normal prose", "Tab", null);
edit("- one", "Tab", "  - one");
edit("  - one", "Tab", "- one", { shift: true });
const selection = edit("- one\n- two", "Tab", "  - one\n  - two", {}, 0, 11);
assert.deepEqual(selection.selection, [2, 15]);
edit("  - one\n  - two", "Tab", "- one\n- two", { shift: true }, 2, 15);
assert.equal(fenceBefore("````js\n```\n", 12).marker, "````");
assert.equal(commands.size, 0);
console.log(`PASS: ${checks} composer cases, selection mapping, fence lengths, and empty command registry.`);
