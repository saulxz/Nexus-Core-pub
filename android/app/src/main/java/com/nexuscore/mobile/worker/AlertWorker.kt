package com.nexuscore.mobile.worker

import android.Manifest
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import androidx.work.CoroutineWorker
import androidx.work.WorkerParameters
import com.nexuscore.mobile.MainActivity
import com.nexuscore.mobile.R
import com.nexuscore.mobile.data.ApiClient
import com.nexuscore.mobile.data.Repository
import com.nexuscore.mobile.data.SessionStore

class AlertWorker(
    appContext: Context,
    params: WorkerParameters,
) : CoroutineWorker(appContext, params) {

    override suspend fun doWork(): Result {
        val store = SessionStore(applicationContext)
        if (!store.hasSession()) return Result.success()
        return try {
            val repo = Repository(ApiClient(store))
            val events = repo.alertEvents(limit = 100).events
            val open = events.count { it.status == "open" }
            if (open > 0) {
                notify(open)
            }
            Result.success()
        } catch (_: Throwable) {
            Result.success()
        }
    }

    private fun notify(count: Int) {
        if (Build.VERSION.SDK_INT >= 33 &&
            ContextCompat.checkSelfPermission(
                applicationContext,
                Manifest.permission.POST_NOTIFICATIONS,
            ) != PackageManager.PERMISSION_GRANTED
        ) {
            return
        }
        val manager = applicationContext.getSystemService(Context.NOTIFICATION_SERVICE)
            as NotificationManager
        if (Build.VERSION.SDK_INT >= 26) {
            val channel = NotificationChannel(
                CHANNEL_ID,
                "Alertas de Nexus-Core",
                NotificationManager.IMPORTANCE_DEFAULT,
            ).apply {
                description = "Alertas abiertas del hub"
            }
            manager.createNotificationChannel(channel)
        }
        val intent = Intent(applicationContext, MainActivity::class.java)
        val pending = PendingIntent.getActivity(
            applicationContext,
            1001,
            intent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        val notification = NotificationCompat.Builder(applicationContext, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_launcher)
            .setContentTitle("Nexus-Core")
            .setContentText("$count alerta(s) abierta(s) en la flota")
            .setContentIntent(pending)
            .setAutoCancel(true)
            .setPriority(NotificationCompat.PRIORITY_DEFAULT)
            .build()
        NotificationManagerCompat.from(applicationContext).notify(NOTIFICATION_ID, notification)
    }

    companion object {
        private const val CHANNEL_ID = "nexus_alerts"
        private const val NOTIFICATION_ID = 1001
    }
}
