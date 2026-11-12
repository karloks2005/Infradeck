---
name: code-simplicity
description: Use when writing, adding or modifying code where there is a risk of excess complexity: unnecessary abstractions, layers, configuration options, extra "extensibility", overdone error handling, or tests, logging and documentation nobody asked for. Do not use for security reviews (use security-audit for that).
---
 
# Keeping code simple
 
The goal is the smallest code that solves the task correctly and is easy to read and change. Shorter code that works is better than longer code that is "more robust" but needlessly complex.
 
## Rules
 
1. **Abstractions only when the task calls for them.** Do not introduce abstract classes, interfaces, factory functions, strategies or layers for something used in one place. Repeating two or three lines is usually better than a premature abstraction.
2. **No parameters or options nobody asked for.** Do not add configuration, flags or parameters for scenarios no one has mentioned.
3. **Error handling only for real inputs.** Validate data that comes from outside (user, file, network, API). Do not guard against states that cannot occur in the context of the task.
4. **No extras nobody asked for.** Do not add logging, tests, documentation or README changes unless they were requested. Comment only where something is not obvious from the code.
   When tests are written (because they were requested or the project requires them), they go into the matching folder inside `tests/` according to the area they protect (`accounts`, `collectors`, `monitoring`, `history`, `assistant`, `security`; the list is in `TESTS.md`, section "Where the tests live"), never directly in `tests/`. If no folder fits, create a new one and add it to that list. File names must be unique across all folders.
5. **Do not change what is not part of the task.** If you see room for improvement in neighbouring code, mention it in your reply, but do not touch it.
6. **A more complex structure needs an explanation.** If you think a bigger design is needed, first briefly explain why and ask, and only then write it.
## Check before handing over
 
Before you hand over the code, read it and ask yourself:
- Can I remove a layer, class or function and keep the behaviour the same?
- Is there a parameter, branch or check that does not match any real case?
- Is the name clear enough that no comment is needed?
If the answer is "yes" to either of the first two questions, cut it down.
 
## Example
 
Task: "Write a function that returns the total of the prices in the cart."
 
Too much:
```python
class PriceCalculator:
    def __init__(self, strategy=None, currency="EUR", rounding=2):
        ...
    def calculate(self, items):
        ...
```
 
Enough:
```python
def total(items):
    return sum(item.price for item in items)
```
 
## Exceptions
 
Complexity is justified when the task explicitly asks for it, when the code goes into a larger system with clearly defined interfaces, or when existing code in the project already uses a particular pattern. In that case follow the existing pattern rather than introducing a new one. If you are not sure, ask the user.
 