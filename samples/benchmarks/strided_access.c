#include <stdio.h>
int main(void) {
    static int a[100000];
    for (int i = 0; i < 100000; i++)
        a[i] = i % 100;

    long long sum = 0;
    for (int i = 0; i < 100000; i += 2)
        sum += a[i];

    printf("%lld\n", sum);
    return 0;
}
