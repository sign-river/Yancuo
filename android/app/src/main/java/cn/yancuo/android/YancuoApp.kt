package cn.yancuo.android

import android.app.Application
import cn.yancuo.android.data.assets.ObjectStore
import cn.yancuo.android.data.credentials.TokenStore
import cn.yancuo.android.data.db.YancuoDb
import cn.yancuo.android.data.ebpack.EbpackImporter
import cn.yancuo.android.data.identity.IdentityStore
import cn.yancuo.android.data.paths.DataPaths
import cn.yancuo.android.data.identity.LocalIdentity
import cn.yancuo.android.data.repo.ProblemRepository

class YancuoApp : Application() {

    lateinit var paths: DataPaths
        private set
    lateinit var identityStore: IdentityStore
        private set
    lateinit var objectStore: ObjectStore
        private set
    lateinit var db: YancuoDb
        private set
    lateinit var problems: ProblemRepository
        private set
    lateinit var tokenStore: TokenStore
        private set
    lateinit var ebpackImporter: EbpackImporter
        private set

    override fun onCreate() {
        super.onCreate()
        paths = DataPaths.from(this)
        identityStore = IdentityStore(paths.identityFile)
        val identity = identityStore.loadOrCreate()
        LocalIdentityHolder.current = identity
        objectStore = ObjectStore(paths.assetObjectsDir)
        db = YancuoDb.openExistingOrCreate(this, paths.database)
        problems = ProblemRepository(db, objectStore)
        tokenStore = TokenStore(this)
        ebpackImporter = EbpackImporter(paths, identityStore)
    }

    /** ebpack 导入后重新打开数据库。 */
    fun reopenAfterImport() {
        YancuoDb.resetInstance()
        db = YancuoDb.openExistingOrCreate(this, paths.database)
        problems = ProblemRepository(db, objectStore)
        LocalIdentityHolder.current = identityStore.loadOrCreate()
    }
}

/** 简单持有当前身份，避免到处传参。 */
object LocalIdentityHolder {
    @Volatile
    var current: LocalIdentity? = null
}
