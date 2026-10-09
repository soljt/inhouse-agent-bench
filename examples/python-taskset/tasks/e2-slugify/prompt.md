We want URL slugs for article titles. Add `slugify(title)` to `textstats`, importable as
`from textstats import slugify`: lower case, words joined by single hyphens, only `a-z` and `0-9` kept, no
leading or trailing hyphen. For example `slugify('  Hello,   World!  ')` is `'hello-world'` and
`slugify('Top 10 Tips')` is `'top-10-tips'`.

When you are done, summarize what you changed and how you verified it.
