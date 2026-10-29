# Paper Trading system — verified DEV release

Implemented and deployed to [DEV Trading](https://sn-next-ui-dev.bluefield-2d27dcf2.centralus.azurecontainerapps.io/trading-agent). Source: branch `fix/trading-system-20261029`, accepted application `7a2ce5b4`, packaging `6f66339a`. The isolated checkout preserves concurrent work in the main checkout.

- [Source review](SOURCE_REVIEW.md): persistence, accounting, controls, shared agent and both clients.
- [DEV acceptance](DEVELOPMENT_ACCEPTANCE.json): live account and local verification.
- [Complete consumer readback](FINAL_CONSUMERS.json):15 DEV updates,21 production images retained, all36 configurations preserved.
- [Exact image verification](PACKAGE_ACCEPTANCE.json):195 Python runtime files per role and all1723 compiled web files.
- [Local/native evidence](LOCAL_ACCEPTANCE.json):212 focused checks,16 web contracts and signed Simulator build17.

The `dev-*.jpg` files show the real DEV account. Native/populated screenshots use a loopback display fixture without broker credentials; no simulated result is presented as the owner's live performance. Initial DEV records preserve the inspection that found and corrected flat-account Sharpe variance. Production and the installed iPhone build16 are unchanged; promoting these four exact images and installing build17 require owner authorization.
