#include <stdio.h>

int main(void) {
    volatile long long a = 0, b = 1;

    for (int i = 0; i < 10000000; i++) {
        long long next = (a + b) % 1000000007;
        a = b;
        b = next;
    }

    printf("%lld\n", b);
    return 0;
}
