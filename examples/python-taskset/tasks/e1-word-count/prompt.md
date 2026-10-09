`word_count` in `textstats` miscounts text pasted from documents: `word_count('hello  world')` returns 3, and a
trailing newline or a tab between words also inflates the count. Make it count words the way a reader would.

When you are done, summarize what you changed and how you verified it.
