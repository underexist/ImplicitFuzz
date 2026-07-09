#include <stdlib.h>

struct Node {
    int value;
    struct Node *next;
    const char *name;
    unsigned long flags;
};

static struct Node *alloc_node(int value)
{
    struct Node *n = (struct Node *)malloc(sizeof(struct Node));
    n->value = value;
    n->next = NULL;
    n->name = "node";
    n->flags = (unsigned long)value;
    return n;
}

static void free_node(struct Node *n)
{
    free(n);
}

static int peek_value(struct Node *n)
{
    return n->value;
}

int accumulate(struct Node *head)
{
    int sum = 0;
    while (head != NULL)
    {
        sum += head->value;
        if (head->name != NULL)
            sum += (int)head->flags;
        head->flags = head->flags + 1UL;
        head = head->next;
    }
    return sum;
}

int main(void)
{
    struct Node *a = alloc_node(1);
    struct Node *b = alloc_node(2);
    b->next = a;
    b->name = "beta";
    b->flags = b->flags | 1UL;
    int sum = accumulate(b);
    sum += peek_value(b);
    free_node(b);
    free_node(a);
    return sum;
}
